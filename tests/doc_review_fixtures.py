from __future__ import annotations

from typing import Any


def sample_file(file_id: int, file_type: str, name: str) -> dict[str, Any]:
    return {
        "id": file_id,
        "relateId": "TEST-RELATE",
        "cautionId": None,
        "fileName": name,
        "fileSize": 1024,
        "fileId": None,
        "fileKey": f"test-file-{file_id}",
        "fileType": file_type,
        "bpmDocId": f"test-doc-{file_id}",
        "isDelete": False,
        "s3PreviewFileUrl": f"https://example.test/preview/{file_id}",
        "s3OpenFileUrl": f"https://example.test/download/{file_id}",
    }


def sample_entity_data() -> dict[str, Any]:
    """Return a fully synthetic EhsConstruct request for offline tests."""
    return {
        "vendorName": "测试供应商有限公司",
        "workContentDesc": "测试设备检修",
        "workDay": 2,
        "workPermitNo": "TEST-2026-001",
        "companyName": "TEST",
        "projectName": "文档审核测试项目",
        "baseName": "TEST-BASE",
        "workRegion": [
            {
                "regionName": "测试区域",
                "regionName1": "测试基地",
                "regionName2": "测试厂房",
                "regionName3": "一层",
                "regionName4": "设备区",
                "detailLocation": "测试设备旁",
            }
        ],
        "constructionProgrammeFileInfoList": [
            sample_file(1, "CONSTRUCTION_APPLY_PROGRAMME", "programme.jpg")
        ],
        "constructionTechDiscloseFileInfoList": [
            sample_file(2, "CONSTRUCTION_APPLY_TECH_DISCLOSE", "tech.jpg")
        ],
        "receptionInfo": {
            "receiverId": "TEST-RECEIVER",
            "receiverName": "测试接待人",
            "receiverPhone": "13800000000",
            "receiverDepartment": "测试部门",
            "receiverDirector": None,
            "receptionDeptDirectorCode": None,
            "receptionPersonnelDirectSuperiorName": "测试上级",
            "receptionPersonnelDirectSuperiorCode": "TEST-SUPERVISOR",
            "receptionPersonnelManagerName": "测试经理",
            "receptionPersonnelManagerCode": "TEST-MANAGER",
            "isEhsChangeName": "否",
        },
        "projectManager": {
            "projectManagerName": "测试项目经理",
            "projectManagerPhone": "13800000001",
            "projectManagerIdCard": "TEST-ID-PM",
        },
        "guardian": {
            "guardianName": "测试监护人",
            "guardianPhone": "13800000002",
            "guardianIdCard": "TEST-ID-GUARDIAN",
        },
        "safetyOfficer": {
            "safetyOfficerCertNo": "TEST-SAFETY-CERT",
            "certExpiryDate": "2030-12-31",
            "certAttachments": [],
        },
        "operator": [
            {
                "operatorName": "测试作业员",
                "operatorIdCard": "TEST-ID-OPERATOR",
                "hasCert": False,
                "certType": "",
                "certNo": "",
                "certExpireDate": "",
                "operatorCertAttachments": None,
                "hasWorkInsurance": True,
                "threeLevelSafetyEducationProof": None,
            }
        ],
        "workInfo": [
            {
                "workType": "高处作业许可",
                "affectedArea": "无",
                "riskIdentification": ["高处坠落"],
                "involvedAreaType": None,
                "riskLevel": "2级",
                "workDate": "2026-08-10~2026-08-11",
            },
            {
                "workType": "吊装作业许可",
                "affectedArea": "无",
                "riskIdentification": ["起重伤害"],
                "involvedAreaType": None,
                "riskLevel": "2级",
                "workDate": "2026-08-10~2026-08-11",
            },
        ],
    }
