"""
Rigorous Evaluation Methodology for Multi-Hazard Weather Prediction
=====================================================================

Phase 3: Rigorous Evaluation Methodology (Eliminate Data Leakage)

Replaces random train-test split with:

  1. Temporal Split: Train on historical timeline, test on future timeline
     (e.g., Jan 2015 - Dec 2020 train, Jan 2021 - Dec 2022 test)
     The model cannot "peek" into the future.

  2. Leave-One-Location-Out (LOLO) Cross-Validation: Train on all cities
     except one, test on the excluded city. Ensures generalization to
     unseen locations (critical for rural deployment).

  3. Metrics: Track R², Brier Skill Score (BSS), and AUC-ROC to measure
     probabilistic reliability, not just point accuracy.

Justification: Random splits give overly optimistic, fake scores.
Real-world disasters require models that generalize across time and geography.
"""

from __future__ import annotations

import logging
import warnings
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    roc_auc_score,
    roc_curve,
    precision_recall_fscore_support,
)
from sklearn.model_selection import BaseCrossValidator

logger = logging.getLogger(__name__)


# ============================================================================
# Temporal Cross-Validation Splitter
# ============================================================================

class TemporalSplitCV(BaseCrossValidator):
    """
    Time-aware cross-validation splitter.

    Splits data chronologically: earlier data for training, later data
    for testing. Never allows future data to leak into training.

    Parameters
    ----------
    n_splits : int
        Number of temporal splits.
    train_size : float
        Fraction of data for training (default 0.8 = 80%).
    gap : int
        Number of samples to exclude between train and test (prevents
        temporal leakage from overlapping windows).
    """

    def __init__(self, n_splits: int = 5, train_size: float = 0.8, gap: int = 0):
        self.n_splits = n_splits
        self.train_size = train_size
        self.gap = gap

    def split(self, X, y=None, groups=None, timestamps=None):
        """
        Generate indices to split data temporally.

        If timestamps are provided, splits are based on time order.
        Otherwise, assumes data is already sorted chronologically.
        """
        n_samples = len(X)

        if timestamps is not None:
            # Sort by timestamp
            if isinstance(timestamps, pd.Series):
                timestamps = pd.to_datetime(timestamps)
            sort_idx = np.argsort(timestamps)
            X_sorted = X[sort_idx] if hasattr(X, '__getitem__') else X
        else:
            sort_idx = np.arange(n_samples)

        # For each split, use expanding window
        for i in range(self.n_splits):
            # Determine split point
            split_ratio = self.train_size * (i + 1) / self.n_splits
            split_point = int(n_samples * split_ratio)

            # Ensure minimum training size
            min_train = max(10, int(n_samples * 0.1))
            if split_point < min_train:
                split_point = min_train

            # Ensure there's test data
            if split_point >= n_samples - 1:
                break

            train_end = split_point
            test_start = split_point + self.gap

            if test_start >= n_samples:
                break

            train_idx = sort_idx[:train_end]
            test_idx = sort_idx[test_start:]

            yield train_idx, test_idx

    def get_n_splits(self, X=None, y=None, groups=None):
        return self.n_splits


# ============================================================================
# Leave-One-Location-Out Cross-Validation Splitter
# ============================================================================

class LeaveOneLocationOutCV(BaseCrossValidator):
    """
    Leave-One-Location-Out (LOLO) cross-validation.

    Trains on all locations except one, tests on the held-out location.
    This ensures the model generalizes to unseen geographic locations
    (critical for rural deployment where new villages may have no data).

    Parameters
    ----------
    locations : array-like
        Location labels for each sample.
    """

    def __init__(self, locations: Optional[np.ndarray] = None):
        self.locations = locations

    def split(self, X, y=None, groups=None):
        """
        Generate LOLO splits.

        Parameters
        ----------
        X : array-like
            Feature matrix.
        y : array-like, optional
            Target values.
        groups : array-like
            Location labels for each sample.
        """
        if groups is None:
            if self.locations is None:
                raise ValueError("Location labels must be provided via 'groups' or constructor.")
            groups = self.locations

        unique_locations = np.unique(groups)

        for test_location in unique_locations:
            test_mask = groups == test_location
            train_mask = ~test_mask

            train_idx = np.where(train_mask)[0]
            test_idx = np.where(test_mask)[0]

            if len(train_idx) > 0 and len(test_idx) > 0:
                yield train_idx, test_idx

    def get_n_splits(self, X=None, y=None, groups=None):
        if groups is not None:
            return len(np.unique(groups))
        elif self.locations is not None:
            return len(np.unique(self.locations))
        return 0


# ============================================================================
# Evaluation Metrics
# ============================================================================

class EvaluationMetrics:
    """
    Comprehensive evaluation metrics for multi-hazard weather prediction.

    Tracks not just R²/accuracy, but also:
      - Brier Skill Score (BSS): probabilistic reliability
      - AUC-ROC: discrimination ability
      - Reliability diagram data
      - Per-hazard metrics
    """

    @staticmethod
    def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
        """
        Compute the Brier Score for probabilistic predictions.

        Brier Score = mean((y_prob - y_true)^2)
        Lower is better. Range: [0, 1].
        """
        y_true_binary = (y_true == y_prob.argmax(axis=1)).astype(int) if y_true.ndim == 1 else y_true
        # For multi-class, use the probability of the true class
        if y_prob.ndim == 2 and y_true.ndim == 1:
            n_samples = y_prob.shape[0]
            brier = np.mean([(y_prob[i, y_true[i]] - 1) ** 2 +
                            np.sum(y_prob[i, np.arange(y_prob.shape[1]) != y_true[i]] ** 2)
                            for i in range(n_samples)])
            return float(brier)
        return float(brier_score_loss(y_true_binary, y_prob[:, 1] if y_prob.ndim == 2 else y_prob))

    @staticmethod
    def brier_skill_score(
        y_true: np.ndarray,
        y_prob: np.ndarray,
        y_prob_climatology: Optional[np.ndarray] = None,
    ) -> float:
        """
        Compute the Brier Skill Score (BSS).

        BSS = 1 - BS_model / BS_climatology

        BSS > 0 means the model outperforms climatology.
        BSS = 1 is perfect. BSS = 0 means no improvement over climatology.
        BSS < 0 means the model is worse than climatology.

        Parameters
        ----------
        y_true : np.ndarray
            True binary labels.
        y_prob : np.ndarray
            Predicted probabilities for the positive class.
        y_prob_climatology : np.ndarray, optional
            Climatological probabilities (baseline). If None, uses
            the base rate of the positive class.
        """
        # Brier score of the model
        if y_prob.ndim == 2:
            # Multi-class: use the probability of the true class
            n_samples = y_prob.shape[0]
            bs_model = np.mean([
                (y_prob[i, y_true[i]] - 1) ** 2 +
                np.sum(y_prob[i, np.arange(y_prob.shape[1]) != y_true[i]] ** 2)
                for i in range(n_samples)
            ])
        else:
            bs_model = brier_score_loss(y_true, y_prob)

        # Brier score of climatology (baseline)
        if y_prob_climatology is not None:
            bs_clim = brier_score_loss(y_true, y_prob_climatology)
        else:
            # Use base rate as climatological forecast
            base_rate = np.mean(y_true) if y_true.ndim == 1 else np.mean(y_true, axis=0)
            if y_prob.ndim == 2:
                bs_clim = np.mean([
                    (base_rate - (y_true[i] == j)) ** 2
                    for i in range(len(y_true))
                    for j in range(y_prob.shape[1])
                ])
            else:
                bs_clim = brier_score_loss(y_true, np.full_like(y_true, base_rate))

        if bs_clim == 0:
            return 1.0 if bs_model == 0 else 0.0

        return float(1.0 - bs_model / bs_clim)

    @staticmethod
    def auc_roc(
        y_true: np.ndarray,
        y_prob: np.ndarray,
        multi_class: str = "ovr",
    ) -> float:
        """
        Compute the Area Under the ROC Curve (AUC-ROC).

        For multi-class, uses one-vs-rest averaging.
        """
        try:
            if y_prob.ndim == 2 and y_prob.shape[1] > 2:
                # Multi-class
                return float(roc_auc_score(y_true, y_prob, multi_class=multi_class, average="macro"))
            elif y_prob.ndim == 2:
                # Binary
                return float(roc_auc_score(y_true, y_prob[:, 1]))
            else:
                return float(roc_auc_score(y_true, y_prob))
        except ValueError:
            # Not enough classes or other issue
            return 0.5

    @staticmethod
    def evaluate(
        y_true: np.ndarray,
        y_pred: np.ndarray,
        y_prob: np.ndarray,
        class_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Comprehensive evaluation of model predictions.

        Parameters
        ----------
        y_true : np.ndarray
            True labels.
        y_pred : np.ndarray
            Predicted labels.
        y_prob : np.ndarray
            Predicted probabilities (shape: n_samples x n_classes).
        class_names : list of str, optional
            Names of the classes.

        Returns
        -------
        dict with all metrics
        """
        results = {}

        # Basic metrics
        results["accuracy"] = float(accuracy_score(y_true, y_pred))

        # Per-class precision, recall, F1
        precision, recall, f1, support = precision_recall_fscore_support(
            y_true, y_pred, average=None, zero_division=0
        )

        if class_names is None:
            class_names = [str(i) for i in np.unique(y_true)]

        results["per_class"] = {}
        for i, name in enumerate(class_names):
            if i < len(precision):
                results["per_class"][name] = {
                    "precision": float(precision[i]),
                    "recall": float(recall[i]),
                    "f1_score": float(f1[i]),
                    "support": int(support[i]),
                }

        # Macro and weighted averages
        p_macro, r_macro, f1_macro, _ = precision_recall_fscore_support(
            y_true, y_pred, average="macro", zero_division=0
        )
        p_weighted, r_weighted, f1_weighted, _ = precision_recall_fscore_support(
            y_true, y_pred, average="weighted", zero_division=0
        )

        results["macro_avg"] = {
            "precision": float(p_macro),
            "recall": float(r_macro),
            "f1_score": float(f1_macro),
        }
        results["weighted_avg"] = {
            "precision": float(p_weighted),
            "recall": float(r_weighted),
            "f1_score": float(f1_weighted),
        }

        # Brier Score
        try:
            results["brier_score"] = EvaluationMetrics.brier_score(y_true, y_prob)
        except Exception:
            results["brier_score"] = None

        # Brier Skill Score
        try:
            results["brier_skill_score"] = EvaluationMetrics.brier_skill_score(y_true, y_prob)
        except Exception:
            results["brier_skill_score"] = None

        # AUC-ROC
        try:
            results["auc_roc"] = EvaluationMetrics.auc_roc(y_true, y_prob)
        except Exception:
            results["auc_roc"] = 0.5

        # R² (for regression-style evaluation of probability calibration)
        try:
            from sklearn.metrics import r2_score
            # One-hot encode y_true
            n_classes = y_prob.shape[1]
            y_true_onehot = np.zeros((len(y_true), n_classes))
            y_true_onehot[np.arange(len(y_true)), y_true] = 1
            results["r2_score"] = float(r2_score(y_true_onehot, y_prob))
        except Exception:
            results["r2_score"] = None

        return results


# ============================================================================
# Comprehensive Evaluator
# ============================================================================

class ComprehensiveEvaluator:
    """
    End-to-end evaluator that runs both temporal and LOLO cross-validation.

    This replaces the random train-test split with rigorous evaluation
    that eliminates data leakage.
    """

    def __init__(
        self,
        temporal_splits: int = 3,
        train_fraction: float = 0.8,
        temporal_gap: int = 0,
    ):
        self.temporal_cv = TemporalSplitCV(
            n_splits=temporal_splits,
            train_size=train_fraction,
            gap=temporal_gap,
        )
        self.lolo_cv = LeaveOneLocationOutCV()
        self.metrics = EvaluationMetrics()

    def evaluate_temporal(
        self,
        model,
        X: np.ndarray,
        y: np.ndarray,
        timestamps: Optional[np.ndarray] = None,
        feature_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate model using temporal cross-validation.

        The model is trained on earlier data and tested on later data.
        No future information leaks into training.
        """
        logger.info("=== Temporal Cross-Validation ===")

        all_results = []
        fold = 0

        for train_idx, test_idx in self.temporal_cv.split(X, y, timestamps=timestamps):
            fold += 1
            X_train, X_test = X[train_idx], X[test_idx]
            y_train, y_test = y[train_idx], y[test_idx]

            logger.info(f"  Fold {fold}: train={len(X_train)}, test={len(X_test)}")

            # Train model
            if hasattr(model, "fit"):
                model.fit(X_train, y_train, feature_names=feature_names)

            # Predict
            y_pred = model.predict(X_test)
            y_prob = model.predict_proba(X_test)

            # Evaluate
            result = self.metrics.evaluate(y_test, y_pred, y_prob)
            result["fold"] = fold
            result["train_size"] = len(X_train)
            result["test_size"] = len(X_test)
            all_results.append(result)

            logger.info(f"    Accuracy: {result['accuracy']:.4f}")
            logger.info(f"    BSS: {result.get('brier_skill_score', 'N/A')}")
            logger.info(f"    AUC-ROC: {result.get('auc_roc', 'N/A')}")

        # Aggregate results
        aggregated = self._aggregate_results(all_results)
        aggregated["evaluation_type"] = "temporal_cv"
        return aggregated

    def evaluate_lolo(
        self,
        model,
        X: np.ndarray,
        y: np.ndarray,
        locations: np.ndarray,
        feature_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate model using Leave-One-Location-Out cross-validation.

        The model is trained on all locations except one, tested on the
        held-out location. Ensures generalization to unseen locations.
        """
        logger.info("=== Leave-One-Location-Out Cross-Validation ===")

        all_results = []
        fold = 0

        for train_idx, test_idx in self.lolo_cv.split(X, y, groups=locations):
            fold += 1
            X_train, X_test = X[train_idx], X[test_idx]
            y_train, y_test = y[train_idx], y[test_idx]

            test_location = np.unique(locations[test_idx])[0]
            logger.info(f"  Fold {fold}: test_location={test_location}, train={len(X_train)}, test={len(X_test)}")

            # Train model
            if hasattr(model, "fit"):
                model.fit(X_train, y_train, feature_names=feature_names)

            # Predict
            y_pred = model.predict(X_test)
            y_prob = model.predict_proba(X_test)

            # Evaluate
            result = self.metrics.evaluate(y_test, y_pred, y_prob)
            result["fold"] = fold
            result["test_location"] = str(test_location)
            result["train_size"] = len(X_train)
            result["test_size"] = len(X_test)
            all_results.append(result)

            logger.info(f"    Accuracy: {result['accuracy']:.4f}")
            logger.info(f"    BSS: {result.get('brier_skill_score', 'N/A')}")
            logger.info(f"    AUC-ROC: {result.get('auc_roc', 'N/A')}")

        # Aggregate results
        aggregated = self._aggregate_results(all_results)
        aggregated["evaluation_type"] = "lolo_cv"
        return aggregated

    def evaluate_combined(
        self,
        model,
        X: np.ndarray,
        y: np.ndarray,
        timestamps: Optional[np.ndarray] = None,
        locations: Optional[np.ndarray] = None,
        feature_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Run both temporal and LOLO evaluation.

        This provides the most rigorous evaluation: the model must
        generalize across both time and geography.
        """
        results = {}

        # Temporal evaluation
        results["temporal"] = self.evaluate_temporal(
            model, X, y, timestamps=timestamps, feature_names=feature_names
        )

        # LOLO evaluation (if locations provided)
        if locations is not None:
            results["lolo"] = self.evaluate_lolo(
                model, X, y, locations, feature_names=feature_names
            )

        return results

    def _aggregate_results(self, all_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Aggregate per-fold results into summary statistics."""
        if not all_results:
            return {}

        # Collect numeric metrics
        numeric_keys = [
            "accuracy", "brier_score", "brier_skill_score",
            "auc_roc", "r2_score",
            "macro_avg.precision", "macro_avg.recall", "macro_avg.f1_score",
            "weighted_avg.precision", "weighted_avg.recall", "weighted_avg.f1_score",
        ]

        aggregated = {
            "n_folds": len(all_results),
            "per_fold": all_results,
        }

        for key in numeric_keys:
            values = []
            for result in all_results:
                # Handle nested keys (e.g., "macro_avg.precision")
                parts = key.split(".")
                val = result
                for part in parts:
                    if isinstance(val, dict) and part in val:
                        val = val[part]
                    else:
                        val = None
                        break
                if val is not None and isinstance(val, (int, float)):
                    values.append(val)

            if values:
                aggregated[f"{key}_mean"] = float(np.mean(values))
                aggregated[f"{key}_std"] = float(np.std(values))
                aggregated[f"{key}_min"] = float(np.min(values))
                aggregated[f"{key}_max"] = float(np.max(values))

        # Per-class metrics
        class_names = set()
        for result in all_results:
            if "per_class" in result:
                class_names.update(result["per_class"].keys())

        for class_name in class_names:
            for metric in ["precision", "recall", "f1_score"]:
                values = []
                for result in all_results:
                    if "per_class" in result and class_name in result["per_class"]:
                        val = result["per_class"][class_name].get(metric)
                        if val is not None:
                            values.append(val)

                if values:
                    aggregated[f"class_{class_name}_{metric}_mean"] = float(np.mean(values))
                    aggregated[f"class_{class_name}_{metric}_std"] = float(np.std(values))

        return aggregated

    def generate_report(self, results: Dict[str, Any]) -> str:
        """Generate a human-readable evaluation report."""
        lines = []
        lines.append("=" * 70)
        lines.append("MULTI-HAZARD WEATHER PREDICTION — EVALUATION REPORT")
        lines.append("=" * 70)
        lines.append("")

        for eval_type, eval_results in results.items():
            lines.append(f"--- {eval_type.upper()} Evaluation ---")
            lines.append(f"  Number of folds: {eval_results.get('n_folds', 'N/A')}")
            lines.append("")

            # Key metrics
            for key in ["accuracy", "brier_skill_score", "auc_roc", "r2_score"]:
                mean_key = f"{key}_mean"
                std_key = f"{key}_std"
                if mean_key in eval_results:
                    lines.append(f"  {key}: {eval_results[mean_key]:.4f} ± {eval_results[std_key]:.4f}")

            lines.append("")

            # Per-class metrics
            if "per_fold" in eval_results:
                class_names = set()
                for fold_result in eval_results["per_fold"]:
                    if "per_class" in fold_result:
                        class_names.update(fold_result["per_class"].keys())

                if class_names:
                    lines.append("  Per-class F1 scores:")
                    for class_name in sorted(class_names):
                        f1_key = f"class_{class_name}_f1_score_mean"
                        if f1_key in eval_results:
                            lines.append(f"    {class_name}: {eval_results[f1_key]:.4f}")

            lines.append("")

        lines.append("=" * 70)
        lines.append("Note: BSS (Brier Skill Score) > 0 indicates the model")
        lines.append("outperforms climatology. AUC-ROC > 0.5 indicates better")
        lines.append("than random discrimination.")
        lines.append("=" * 70)

        return "\n".join(lines)
