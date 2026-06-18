"""결과 데이터 모델. 불변(frozen) 데이터클래스로 상태를 표현한다."""
from dataclasses import dataclass, field, asdict, replace
from enum import Enum


class Status(str, Enum):
    """수집 결과 상태. None(정보 없음)과 오류를 명확히 구분한다."""

    SUCCESS = "success"            # 공식 홈페이지에서 인재상 수집됨
    REFERENCE_ONLY = "reference_only"  # 홈페이지엔 없고 외부 참고에서만 발견
    NO_DATA = "no_data"           # 홈페이지는 정상이나 인재상이 존재하지 않음(=None)
    URL_NOT_FOUND = "url_not_found"   # 공식 홈페이지 후보를 못 찾음
    VERIFY_FAILED = "verify_failed"   # 후보는 있으나 해당 기업 소유로 검증 실패
    CRAWL_ERROR = "crawl_error"   # 홈페이지 요청/파싱 오류
    EXTRACT_ERROR = "extract_error"   # LLM 추출 오류


@dataclass(frozen=True)
class Result:
    """한 기업의 인재상 수집 결과(불변)."""

    company_name: str
    status: Status
    official_url: str | None = None
    url_verified: bool = False
    verify_reason: str | None = None
    talent_values: str | None = None          # 공식 홈페이지 근거(신뢰)
    talent_values_source: str | None = None
    business_description: str | None = None
    reference_talent_values: str | None = None  # 외부 참고(낮은 신뢰)
    reference_urls: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None

    def with_values(self, **changes) -> "Result":
        """불변 갱신: 변경된 필드만 적용한 새 객체 반환."""
        return replace(self, **changes)

    @property
    def is_error(self) -> bool:
        return self.status in (
            Status.CRAWL_ERROR, Status.EXTRACT_ERROR,
            Status.URL_NOT_FOUND, Status.VERIFY_FAILED,
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        data["reference_urls"] = list(self.reference_urls)
        return data
