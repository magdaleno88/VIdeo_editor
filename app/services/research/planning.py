from app.schemas.domain import ResearchQuery, ResearchQuestion, VideoVisualAnalysis


class ResearchPlanner:
    def __init__(self, max_queries: int) -> None:
        self.max_queries = max_queries

    def build(
        self, analysis: VideoVisualAnalysis, fallback_object: str
    ) -> tuple[list[ResearchQuestion], list[ResearchQuery]]:
        object_name = analysis.detected_object or fallback_object
        process = analysis.detected_process
        process_label = process or "the visible manufacturing process"
        questions = [
            ResearchQuestion(
                kind="GENERAL_PROCESS",
                question=f"What processes can manufacture {object_name} with the visible changes?",
            ),
            ResearchQuestion(
                kind="GENERAL_PROCESS",
                question=f"What evidence distinguishes {process_label} from similar processes?",
            ),
            ResearchQuestion(
                kind="GENERAL_PROCESS",
                question="Which machine types normally perform the candidate processes?",
            ),
            ResearchQuestion(
                kind="GENERAL_PROCESS",
                question="What are the documented manufacturing steps and material changes?",
            ),
            ResearchQuestion(
                kind="CLAIM_VERIFICATION",
                question="Which technical claims require stronger evidence or must remain unknown?",
            ),
            ResearchQuestion(
                kind="CLAIM_VERIFICATION",
                question="Do authoritative sources disagree about process conditions or variants?",
            ),
        ]
        raw_queries = [
            ("GENERAL_PROCESS", f"{object_name} manufacturing process technical documentation"),
            ("GENERAL_PROCESS", f"{object_name} {process or 'forming'} process machinery"),
            (
                "CLAIM_VERIFICATION",
                f"{process or object_name} manufacturing process standard technical explanation",
            ),
            (
                "CLAIM_VERIFICATION",
                f"{process or object_name} process material deformation evidence",
            ),
            (
                "CLAIM_VERIFICATION",
                f"{process or object_name} manufacturing process variants limitations",
            ),
        ]
        queries = [
            ResearchQuery(kind=kind, query=query) for kind, query in raw_queries[: self.max_queries]
        ]
        return questions, queries
