
from pydantic import BaseModel


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
    fileName: str
    s3PreviewFileUrl: str


class ReceptionInfo(BaseModel):
    receiverId: str
    receiverName: str
    receiverPhone: str
    receiverDepartment: str
    receiverDirector: str | None = None
    receptionDeptDirectorCode: str | None = None
    receptionPersonnelDirectSuperiorName: str
    receptionPersonnelDirectsuperiorcode: str
    receptionPersonnelManagerName:str
    receptionPersonnellanagercode: str
    isEhsChangeName: str


class ProjectManager(BaseModel):
    projectManagerName: str
    projectHanagerPhone:str
    projectManagerIdCard: str


class Guardian(BaseModel):
    guardianName: str
    guardianPhone: str
    guardianIdCard: str


class Safetyofficer(BaseModel): 
    safetyofficerCertNo: str | None = None
    certExpiryDate:str
    certAttachments: list[FileInfo] = []


class Operator(BaseModel):
    operatorName:str
    openatorIdcand: str
    hascert:bool
    certType: str
    certNo:str
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
    workcontentDesc: str
    workDay:int
    WorkPermitNo: str
    companyName:str
    projectName:str
    baseName: str
    workRegion: list[WorkRegion]
    # 施工方案书
    constructionProgrammeFileInfolist: list[FileInfo]
    # 安全交底书
    constructionTechDiscloseFileInfoList: list[FileInfo]
    receptionInfo: ReceptionInfo
    projecthanager: ProjectManager
    guardian: Guardian
    #证书
    safetyofficer: Safetyofficer
    #作业员&证书
    operator: list[Operator]
    workInfo: list[WorkInfo]

