from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import pickle
from typing import Any, Iterable

from agent_health.tool_outcome_features import build_tool_outcome_features, tool_outcome_feature_text
from agent_health.tool_outcome_taxonomy import TOOL_OUTCOME_DECISION_LABELS, validate_tool_outcome_decision_label, validate_reason_code


class ToolOutcomeModelUnavailable(RuntimeError):
    """Raised when optional ML dependencies are not installed."""


@dataclass(frozen=True)
class ModelArtifact:
    model_name: str
    model_version: str
    artifact_path: str
    training_record_count: int
    accepted_label_count: int
    metrics: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcomeDecision:
    label: str
    is_tool_outcome: bool | None
    reason_code: str | None
    reason_confidence: float | None
    confidence: float
    uncertainty: float
    decision_source: str
    model_name: str | None = None
    model_version: str | None = None
    should_defer_to_llm: bool = False
    budget_fallback: bool = False
    evidence_summary: str | None = None

    def __post_init__(self) -> None:
        validate_tool_outcome_decision_label(self.label)
        validate_reason_code(self.reason_code)


class TfidfToolOutcomeReviewerModel:
    model_name = "tfidf_logistic_tool_outcome"

    def __init__(self, pipeline: Any, *, model_version: str, label_set: list[str], training_record_count: int, feature_schema_version: str = "tool_outcome_features_v1"):
        self.pipeline = pipeline
        self.model_version = model_version
        self.label_set = label_set
        self.training_record_count = training_record_count
        self.feature_schema_version = feature_schema_version

    def predict(self, features: dict[str, Any]) -> ToolOutcomeDecision:
        text = tool_outcome_feature_text(features)
        probabilities = self.pipeline.predict_proba([text])[0]
        classes = [str(c) for c in self.pipeline.classes_]
        ranked = sorted(zip(classes, probabilities), key=lambda item: float(item[1]), reverse=True)
        label, probability = ranked[0]
        confidence = float(probability)
        second = float(ranked[1][1]) if len(ranked) > 1 else 0.0
        return ToolOutcomeDecision(
            label=label,
            is_tool_outcome=True if label == "problem" else False if label == "ok" else None,
            reason_code=None,
            reason_confidence=None,
            confidence=confidence,
            uncertainty=max(0.0, 1.0 - confidence),
            decision_source="ml_model",
            model_name=self.model_name,
            model_version=self.model_version,
            evidence_summary=f"top_label_margin={confidence - second:.3f}",
        )

    def save(self, output_dir: str | Path) -> ModelArtifact:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        path = output / "tool-outcome-reviewer-model.pkl"
        payload = {
            "schema_version": "ariadne_ml_first_tool_outcome_reviewer_model_v1",
            "model_name": self.model_name,
            "model_version": self.model_version,
            "label_set": self.label_set,
            "training_record_count": self.training_record_count,
            "feature_schema_version": self.feature_schema_version,
            "pipeline": self.pipeline,
        }
        with path.open("wb") as handle:
            pickle.dump(payload, handle)
        return ModelArtifact(self.model_name, self.model_version, str(path), self.training_record_count, self.training_record_count, {"label_set": self.label_set})

    @classmethod
    def load(cls, path: str | Path) -> "TfidfToolOutcomeReviewerModel":
        try:
            with Path(path).open("rb") as handle:
                payload = pickle.load(handle)
        except ModuleNotFoundError as exc:
            raise ToolOutcomeModelUnavailable("Install ariadne-eval[ml] to load tool_outcome ML models") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != "ariadne_ml_first_tool_outcome_reviewer_model_v1":
            raise ValueError("not an Ariadne ML-first tool_outcome model")
        labels = [str(label) for label in payload.get("label_set") or []]
        if not set(labels).issubset(TOOL_OUTCOME_DECISION_LABELS):
            raise ValueError("tool_outcome model contains non-decision labels")
        return cls(
            payload["pipeline"],
            model_version=str(payload["model_version"]),
            label_set=labels,
            training_record_count=int(payload.get("training_record_count") or 0),
            feature_schema_version=str(payload.get("feature_schema_version") or "tool_outcome_features_v1"),
        )


def train_tfidf_tool_outcome_reviewer_model(samples: Iterable[dict[str, Any]], *, model_version: str = "local") -> TfidfToolOutcomeReviewerModel:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[reportMissingImports]
        from sklearn.linear_model import LogisticRegression  # type: ignore[reportMissingImports]
        from sklearn.pipeline import make_pipeline  # type: ignore[reportMissingImports]
    except ModuleNotFoundError as exc:
        raise ToolOutcomeModelUnavailable("Install ariadne-eval[ml] to train tool_outcome ML models") from exc
    rows = [row for row in samples if row.get("label")]
    labels = [validate_tool_outcome_decision_label(row.get("label")) for row in rows]
    if len(set(labels)) < 2:
        raise ValueError("need at least two distinct tool_outcome decision labels")
    texts = [str(row.get("text") or tool_outcome_feature_text(build_tool_outcome_features(row))) for row in rows]
    weights = [float(row.get("weight") or 1.0) for row in rows]
    pipeline = make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), lowercase=True),
        LogisticRegression(max_iter=1000, class_weight="balanced"),
    )
    pipeline.fit(texts, labels, logisticregression__sample_weight=weights)
    return TfidfToolOutcomeReviewerModel(pipeline, model_version=model_version, label_set=sorted(set(labels)), training_record_count=len(rows))


def smoke_check_tool_outcome_reviewer_model(path: str | Path) -> bool:
    model = TfidfToolOutcomeReviewerModel.load(path)
    if not set(model.label_set).issubset(TOOL_OUTCOME_DECISION_LABELS):
        return False
    model.predict({"tool_name": "terminal", "tool_result_text": "done"})
    return True
