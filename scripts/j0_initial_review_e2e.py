#!/usr/bin/env python3
"""J0 联调 e2e：初审 C-01 → Python 执行 → C-02 回调 Java 全链路验证。

用法（凭据经环境变量注入，不落库不进 git）：
  MYSQL_HOST=127.0.0.1 MYSQL_USER=root MYSQL_PASSWORD=... MYSQL_DB=cps \
  PYTHON_BASE=http://127.0.0.1:8000/api/v1 ISSUE=990002 \
  .venv/bin/python scripts/j0_initial_review_e2e.py

断言链：
  1) C-01 POST /agent/rectifications → 202 + review_task_ref（幂等键 cps-rectify-{issue}-v{n}）
  2) 轮询 MySQL cps_initial_review_task 直至终态（预期 COMPLETED，error_code 空）
  3) cps_initial_review_result/item 落库且 L/P 实际值非空、无附件检查 SKIPPED
  4) 同载荷重放 → 202 + replayed=true（缓存语义）
  5) 同键不同载荷 → 409 ReplayConflict
  6) C-03 GET /agent/rectifications/{ref} → 200
  7) 清理种子数据（幂等可重跑）

J0 战果（2026-09-22）：首次运行暴露两处真实跨端 bug（agent_callbacks.py 读
app.state.datasources KeyError；C-02 DTO task_id 声明 Long 收字符串引用 400），
修复后全链路 PASS。详见 docs/开发进展日志.md J0 章节。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import pymysql

MYSQL = dict(
    host=os.environ.get('MYSQL_HOST', '127.0.0.1'),
    user=os.environ.get('MYSQL_USER', 'root'),
    password=os.environ.get('MYSQL_PASSWORD', ''),
    database=os.environ.get('MYSQL_DB', 'cps'),
)
BASE = os.environ.get('PYTHON_BASE', 'http://127.0.0.1:8000/api/v1')
C01 = f'{BASE}/agent/rectifications'
C03 = f'{BASE}/agent/rectifications/{{}}'
ISSUE = int(os.environ.get('ISSUE', '990002'))
SUB = ISSUE * 10 + 1
VER = 1
IDEM = f'cps-rectify-{ISSUE}-v{VER}'

# 三段文本均满足 D-22 规则：L≥15、10×P≤L、无连续标点
BODY = {
    'issue_id': str(ISSUE), 'submission_id': str(SUB), 'version_no': VER,
    'reason': '设备已全面检修并完成更换损坏配件，运行恢复正常，现场已清理完毕。',
    'short_term_measure': '当日停机更换配件并复核接线，安排专人值守观察两小时。',
    'long_term_measure': '纳入季度预防性维护计划，增加巡检频次并培训操作人员。',
    'before_attachments': [], 'after_attachments': [],
    'issue_snapshot': {
        'issue_code': f'J0-E2E-{ISSUE}', 'title': 'J0 联调模拟问题', 'severity': 'MEDIUM',
    },
}

def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')

def main():
    assert MYSQL['password'], 'MYSQL_PASSWORD 未设置（凭据不进脚本）'
    conn = pymysql.connect(**MYSQL)
    cur = conn.cursor()
    cur.execute(
        "DELETE i FROM cps_initial_review_item i JOIN cps_initial_review_task t "
        "ON i.task_id=t.id WHERE t.issue_id=%s", (ISSUE,))
    cur.execute(
        "DELETE r FROM cps_initial_review_result r JOIN cps_initial_review_task t "
        "ON r.task_id=t.id WHERE t.issue_id=%s", (ISSUE,))
    cur.execute("DELETE FROM cps_initial_review_task WHERE issue_id=%s", (ISSUE,))
    cur.execute("""INSERT INTO cps_initial_review_task
        (issue_id, submission_id, version_no, status, idempotency_key, submitted_at, timeout_at)
        VALUES (%s,%s,%s,'RUNNING',%s,NOW(),DATE_ADD(NOW(), INTERVAL 600 SECOND))""",
        (ISSUE, SUB, VER, IDEM))
    task_id = cur.lastrowid
    conn.commit()
    print(f'[seed] java task id={task_id} idem={IDEM}')

    st, resp = http('POST', C01, BODY)
    print(f'[C-01] status={st} resp={json.dumps(resp, ensure_ascii=False)[:400]}')
    assert st == 202, f'C-01 expected 202 got {st}'
    ref = resp.get('review_task_ref') or resp.get('task_id')
    assert ref, f'C-01 response missing ref: {resp}'
    cur.execute(
        "UPDATE cps_initial_review_task SET review_task_ref=%s WHERE id=%s",
        (str(ref), task_id))
    conn.commit()

    final = None
    for _ in range(60):
        time.sleep(3)
        cur.execute(
            "SELECT status, error_code, completed_at, retry_count "
            "FROM cps_initial_review_task WHERE id=%s", (task_id,))
        row = cur.fetchone()
        if row and row[0] != 'RUNNING':
            final = row
            break
    assert final, '任务 180s 未到终态：查 /tmp/cps-python.log 回调与 /tmp/cps-java.log'
    print(f'[assert] task 终态={final[0]} error_code={final[1]} retry={final[3]}')
    assert final[0] == 'COMPLETED' and final[1] is None, f'非预期终态: {final}'

    cur.execute("SELECT COUNT(*) FROM cps_initial_review_result WHERE task_id=%s", (task_id,))
    assert cur.fetchone()[0] == 1, 'result 行数≠1'
    cur.execute("""SELECT check_type, field_name, verdict, text_length, punctuation_count, ratio_ok
        FROM cps_initial_review_item WHERE task_id=%s ORDER BY id""", (task_id,))
    items = cur.fetchall()
    for r in items:
        print(f'[item] {r}')
    text_items = [r for r in items if r[0] in ('TEXT_LENGTH', 'PUNCTUATION_RATIO')]
    assert len(text_items) == 6 and all(r[3] for r in text_items), '文本检查项应 6 条且 L 值非空'
    skipped = [r for r in items if r[0] in ('IMAGE_COMPARE', 'MEASURE_SIMILARITY')]
    assert all(r[2] == 'SKIPPED' for r in skipped), '无附件检查应为 SKIPPED（不伪造通过）'

    st2, resp2 = http('POST', C01, BODY)
    print(f'[replay] status={st2} replayed={resp2.get("replayed")}')
    assert st2 == 202 and resp2.get('replayed') is True, f'replay expected 202+replayed got {st2}'

    conflict_body = dict(BODY)
    conflict_body['reason'] = '冲突载荷：同幂等键不同内容的探测请求。'
    st2b, _ = http('POST', C01, conflict_body)
    print(f'[conflict] status={st2b}')
    assert st2b == 409, f'conflict expected 409 got {st2b}'

    st3, resp3 = http('GET', C03.format(ref))
    print(f'[C-03] status={st3} status_field={resp3.get("status")} overall={resp3.get("overall")}')
    assert st3 == 200 and resp3.get('status') == 'COMPLETED'

    cur.execute("DELETE FROM cps_initial_review_item WHERE task_id=%s", (task_id,))
    cur.execute("DELETE FROM cps_initial_review_result WHERE task_id=%s", (task_id,))
    cur.execute("DELETE FROM cps_initial_review_task WHERE id=%s", (task_id,))
    conn.commit()
    conn.close()
    print('J0-E2E-RESULT: PASS')

if __name__ == '__main__':
    sys.exit(main())
