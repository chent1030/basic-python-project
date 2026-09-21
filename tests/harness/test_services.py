import pytest

from app.harness.kernel import Artifacts, Mailbox, Observer, Scope, SQLiteRepository, Workspace
from app.harness.kernel.domain.models import Conflict, Forbidden


@pytest.fixture
def repository(tmp_path):
    store = SQLiteRepository(tmp_path / "state.sqlite")
    yield store
    store.close()


def test_workspace_private_and_path_restrictions(tmp_path):
    first = Workspace(tmp_path, Scope("tenant", "task", "run1", "invoke", "attempt"))
    second = Workspace(tmp_path, Scope("tenant", "task", "run2", "invoke", "attempt"))
    first.write("report.md", "first")
    second.write("report.md", "second")
    assert first.read("report.md") == "first"
    assert second.read("report.md") == "second"
    for path in ("../outside", "/etc/passwd"):
        with pytest.raises(Forbidden):
            first.read(path)
    (first.root / "link").symlink_to(tmp_path)
    with pytest.raises(Forbidden):
        first.write("link/escape", "denied")


def test_artifacts_versions_sharing_and_cross_tenant(repository):
    owner = Artifacts(repository, Scope("tenant", "task", "run1", "owner"))
    reader = Artifacts(repository, Scope("tenant", "task", "run1", "reader"))
    other = Artifacts(repository, Scope("tenant", "task", "run2", "reader"))
    published = owner.publish("report", "version one")
    with pytest.raises(Forbidden):
        reader.read(published["id"])
    with pytest.raises(Conflict):
        owner.publish("report", "wrong")
    next_version = owner.publish("report", "version two", expected_version=1, readers=("reader",))
    assert reader.read(next_version["id"]) == b"version two"
    assert owner.read(published["id"]) == b"version one"
    with pytest.raises(Forbidden):
        other.read(next_version["id"])
    repository.put("tenant", "run", "run2", {"id": "run2"})
    owner.grant(next_version["id"], "run2")
    assert other.read(next_version["id"]) == b"version two"
    with pytest.raises(Forbidden):
        Artifacts(repository, Scope("another", "task", "run2")).read(next_version["id"])


def test_mailbox_idempotency_and_acknowledgement(repository):
    sender = Mailbox(repository, Scope("tenant", "task", "run", "sender"))
    receiver = Mailbox(repository, Scope("tenant", "task", "run", "receiver"))
    message_id = sender.send("receiver", {"fact": 1}, key="one")
    assert sender.send("receiver", {"fact": 1}, key="one") == message_id
    with pytest.raises(Conflict):
        sender.send("receiver", {"fact": 2}, key="one")
    assert len(receiver.receive()) == 1
    receiver.acknowledge(message_id)
    assert receiver.receive() == []
    with pytest.raises(Forbidden):
        sender.acknowledge(message_id)


async def test_observer_failure_isolated_and_replay(repository):
    seen = []

    async def observe(event, memory):
        seen.append(event["cursor"])
        if len(seen) == 1:
            raise ValueError("observer failure")
        memory.propose("tenant", "test", "candidate", source_run="run", key=str(event["cursor"]))

    cursor = repository.event("tenant", "run", "invocation.succeeded", {"agent": "worker"})
    observer = Observer(repository, "memory", observe)
    assert await observer.drain("tenant", "run") == 0
    assert await observer.drain("tenant", "run") >= cursor
    assert seen == [cursor, cursor]
    await observer.drain("tenant", "run")
    assert len(list(repository.scan("tenant", "memory"))) == 1
