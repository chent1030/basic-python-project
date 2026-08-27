from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field


class HttpBinResponse(BaseModel):
    code: int
    message: str


class WorkRegion(BaseModel):
    regionName: str
    regionName1: str
    regionName2: str
    regionName3: str | None = None
    regionName4: str | None = None
    detailLocation: str = ""


class FileInfo(BaseModel):
    id: int
    relateId: str | None = None
    cautionId: str | None = None
    fileName: str
    fileSize: int
    fileId: str | None = None
    fileKey: str
    fileType: str
    bpmDocId: str
    isDelete: bool
    s3PreviewFileUrl: str
    s3OpenFileUrl: str


class ReceptionInfo(BaseModel):
    receiverId: str
    receiverName: str
    receiverPhone: str
    receiverDepartment: str
    receiverDirector: str | None = None
    receptionDeptDirectorCode: str | None = None
    receptionPersonnelDirectSuperiorName: str
    receptionPersonnelDirectSuperiorCode: str
    receptionPersonnelManagerName: str
    receptionPersonnelManagerCode: str
    isEhsChangeName: str


class ProjectManager(BaseModel):
    projectManagerName: str
    projectManagerPhone: str
    projectManagerIdCard: str


class Guardian(BaseModel):
    guardianName: str
    guardianPhone: str
    guardianIdCard: str


class SafetyOfficer(BaseModel):
    safetyOfficerCertNo: str | None = None
    certExpiryDate: str
    certAttachments: list[FileInfo] = Field(default_factory=list)


class Operator(BaseModel):
    operatorName: str
    operatorIdCard: str
    hasCert: bool
    certType: str
    certNo: str
    certExpireDate: str
    operatorCertAttachments: list[FileInfo] | None = None
    hasWorkInsurance: bool
    threeLevelSafetyEducationProof: list[FileInfo] | None = None


class WorkInfo(BaseModel):
    workType: str
    affectedArea: str | None = None
    riskIdentification: list[str] | None = None
    involvedAreaType: list[str] | None = None
    riskLevel: str
    workDate: str


class EhsConstruct(BaseModel):
    vendorName: str
    workContentDesc: str
    workDay: int
    workPermitNo: str
    companyName: str
    projectName: str
    baseName: str
    workRegion: list[WorkRegion]
    # 施工方案书
    constructionProgrammeFileInfoList: list[FileInfo] = Field(
        validation_alias=AliasChoices(
            "constructionProgrammeFileInfoList",
            "constructionProgrammeFileInfolist",
        )
    )
    # 安全交底书
    constructionTechDiscloseFileInfoList: list[FileInfo]
    # 动火作业施工交底书(仅勾选动火作业时需要)
    hotWorkTechDiscloseFileInfoList: list[FileInfo] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "hotWorkTechDiscloseFileInfoList",
            "hotWorkDiscloseFileInfoList",
            "constructionHotWorkFileInfoList",
        ),
    )
    # 页面/流程系统传入的发起时间；缺失时签字日期规则进入人工复核
    processInitiatedAt: datetime | None = None
    receptionInfo: ReceptionInfo
    projectManager: ProjectManager
    guardian: Guardian
    # 证书
    safetyOfficer: SafetyOfficer
    # 作业员&证书
    operator: list[Operator]
    workInfo: list[WorkInfo]
