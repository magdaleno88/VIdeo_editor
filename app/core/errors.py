class ApplicationError(Exception):
    status_code = 400


class ConfigurationError(ApplicationError):
    status_code = 503


class NotFoundError(ApplicationError):
    status_code = 404


class ConflictError(ApplicationError):
    status_code = 409


class ProviderError(ApplicationError):
    status_code = 502


class AssetUnavailableError(ApplicationError):
    status_code = 502


class AssetTooLargeError(ApplicationError):
    status_code = 413


class UnsupportedMediaError(ApplicationError):
    status_code = 415


class AnalysisError(ApplicationError):
    status_code = 502


class AnalysisTimeoutError(AnalysisError):
    status_code = 504


class AnalysisValidationError(AnalysisError):
    pass


class SearchProviderError(ApplicationError):
    status_code = 502


class DocumentFetchError(ApplicationError):
    status_code = 502


class ResearchError(ApplicationError):
    status_code = 502


class ResearchTimeoutError(ResearchError):
    status_code = 504


class ResearchValidationError(ResearchError):
    pass


class InsufficientEvidenceError(ApplicationError):
    status_code = 422


class ScriptGenerationError(ApplicationError):
    status_code = 502


class ScriptGenerationTimeoutError(ScriptGenerationError):
    status_code = 504


class ScriptValidationError(ScriptGenerationError):
    pass


class NarrationError(ApplicationError):
    status_code = 502


class NarrationTimeoutError(NarrationError):
    status_code = 504


class NarrationValidationError(NarrationError):
    pass


class AudioStorageError(NarrationError):
    pass


class RenderPlanningError(ApplicationError):
    status_code = 422


class RenderConfigurationError(ConfigurationError):
    pass


class RenderProcessError(ApplicationError):
    status_code = 502

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class RenderTimeoutError(RenderProcessError):
    status_code = 504


class RenderValidationError(RenderProcessError):
    pass


class RenderStorageError(RenderProcessError):
    pass
