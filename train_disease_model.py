#!/usr/bin/env python
"""
train_disease_model.py
======================
Command-line training script for the plant disease deep-learning classifier.

Usage
-----
# 1. Train with synthetic data (always works, no real images needed)
python train_disease_model.py --synthetic --epochs 15 --n-per-class 300

# 2. Train with real images (PlantVillage-style directory layout)
python train_disease_model.py --data-dir ./data/plantvillage --epochs 20 --batch-size 32

# 3. Evaluate an existing model
python train_disease_model.py --evaluate --synthetic

The trained model is saved to ``models/plant_disease_model.pth`` along with
``models/plant_disease_classes.json`` and
``models/plant_disease_class_weights.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import os
from pathlib import Path
from datetime import datetime

# Ensure UTF-8 output for emoji/Unicode in logs (important on Windows cp1252)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from plant_disease_model import (  # noqa: E402
    PlantDiseaseClassifier,
    TORCH_AVAILABLE,
    DEFAULT_DISEASE_CLASSES,
    DISEASE_INFO,
    IMAGE_SIZE,
    MODEL_PATH,
    CLASSES_PATH,
    CLASS_WEIGHTS_PATH,
    ENGINE_VERSION,
    _SyntheticDiseaseDataset,
)


def log(msg: str):
    """Print with flush for real-time logging."""
    print(f"[{datetime.utcnow().isoformat()}Z] {msg}", flush=True)


def train_synthetic(args: argparse.Namespace) -> dict:
    """Train the model using synthetic leaf images."""
    log("=" * 60)
    log("Plant Disease Deep Learning Classifier — Synthetic Training")
    log("=" * 60)

    if not TORCH_AVAILABLE:
        log("❌ PyTorch is not installed. Install with:")
        log("   pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu")
        return {"success": False, "error": "torch not installed"}

    clf = PlantDiseaseClassifier()

    classes = args.classes or DEFAULT_DISEASE_CLASSES
    log(f"Classes ({len(classes)}): {', '.join(classes)}")
    log(f"Epochs: {args.epochs} | Images per class: {args.n_per_class} | "
        f"Batch size: {args.batch_size} | LR: {args.learning_rate}")
    log(f"Image size: {IMAGE_SIZE}x{IMAGE_SIZE} | Engine: {ENGINE_VERSION}")
    log("-" * 60)

    result = clf.train_from_synthetic(
        classes=classes,
        n_per_class=args.n_per_class,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        seed=args.seed,
        log_fn=log,
    )

    log("-" * 60)
    log(f"✅ Training complete: {json.dumps(result, indent=2)}")
    log(f"   Model saved to: {MODEL_PATH}")

    return result


def train_real(args: argparse.Namespace) -> dict:
    """Train the model using real images from a PlantVillage-style directory."""
    log("=" * 60)
    log("Plant Disease Deep Learning Classifier — Real Image Training")
    log("=" * 60)

    if not TORCH_AVAILABLE:
        log("❌ PyTorch is not installed.")
        return {"success": False, "error": "torch not installed"}

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        log(f"❌ Data directory not found: {data_dir}")
        log("   Expected structure:")
        log("   <data_dir>/<class_name>/img1.jpg")
        log("   <data_dir>/<class_name>/img2.jpg")
        log("   ...")
        return {"success": False, "error": f"directory not found: {data_dir}"}

    clf = PlantDiseaseClassifier()

    # Count images per class
    class_counts = {}
    for d in sorted(data_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            class_counts[d.name] = len(list(d.glob("*.jpg")) + list(d.glob("*.png")))

    total_images = sum(class_counts.values())
    log(f"Dataset: {data_dir}")
    log(f"Classes: {len(class_counts)} | Total images: {total_images}")
    for cls, cnt in class_counts.items():
        log(f"  {cls}: {cnt} images")

    log("-" * 60)
    log(f"Epochs: {args.epochs} | Batch size: {args.batch_size} | LR: {args.learning_rate}")
    log(f"Image size: {IMAGE_SIZE}x{IMAGE_SIZE} | Engine: {ENGINE_VERSION}")
    log("-" * 60)

    result = clf.train_from_directory(
        data_dir=args.data_dir,
        val_split=args.val_split,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        seed=args.seed,
        log_fn=log,
    )

    log("-" * 60)
    log(f"✅ Training complete: {json.dumps(result, indent=2)}")

    return result


def evaluate(args: argparse.Namespace) -> dict:
    """Evaluate the model on the synthetic dataset (or real directory)."""
    log("=" * 60)
    log("Plant Disease Model — Evaluation")
    log("=" * 60)

    if not TORCH_AVAILABLE:
        log("❌ PyTorch is not installed.")
        return {"success": False, "error": "torch not installed"}

    clf = PlantDiseaseClassifier()
    if not clf.is_trained:
        log("❌ No trained model found. Train first with --synthetic or --data-dir.")
        return {"success": False, "error": "model not trained"}

    # Run inference on the whole evaluation set
    import torch  # noqa: E402
    from torch.utils.data import DataLoader  # noqa: E402

    # Use the same dataset for evaluation
    if args.data_dir:
        data_dir = Path(args.data_dir)
        from torchvision.datasets import ImageFolder
        from torchvision import transforms as T

        dataset = ImageFolder(
            root=str(data_dir),
            transform=T.Compose([
                T.Resize((IMAGE_SIZE, IMAGE_SIZE)),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ]),
        )
        loader = DataLoader(dataset, batch_size=32, shuffle=False)  # noqa: F821
    else:
        dataset = _SyntheticDiseaseDataset(clf.classes, n_per_class=args.n_per_class, seed=args.seed + 1000)
        loader = DataLoader(dataset, batch_size=32, shuffle=False)  # noqa: F821

    correct = 0
    total = 0
    class_correct = [0] * len(clf.classes)
    class_total = [0] * len(clf.classes)

    clf.model.eval()
    device = clf.device
    with torch.no_grad():
        for images, labels in loader:
            images, labels = images.to(device), labels.to(device)
            outputs = clf.model(images)
            _, predicted = torch.max(outputs, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()
            for i in range(labels.size(0)):
                label = labels[i].item()
                class_total[label] += 1
                if predicted[i].item() == label:
                    class_correct[label] += 1

    overall_acc = correct / max(total, 1)
    log(f"Overall accuracy: {overall_acc:.4f} ({correct}/{total})")

    class_acc = {}
    for i, cls_name in enumerate(clf.classes):
        if class_total[i] > 0:
            acc = class_correct[i] / class_total[i]
            class_acc[cls_name] = round(acc, 4)
            log(f"  {cls_name}: {acc:.4f} ({class_correct[i]}/{class_total[i]})")

    return {
        "success": True,
        "overall_accuracy": round(overall_acc, 4),
        "class_accuracies": class_acc,
        "total_samples": total,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Train or evaluate the plant disease image classifier (ResNet-18 CNN).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python train_disease_model.py --synthetic --epochs 15 --n-per-class 300
  python train_disease_model.py --data-dir ./data/plantvillage --epochs 20
  python train_disease_model.py --evaluate --synthetic
        """,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--synthetic", action="store_true",
                      help="Train using synthetically generated leaf images (always works).")
    mode.add_argument("--data-dir", type=str, default=None,
                      help="Path to PlantVillage-style directory for real-image training.")
    mode.add_argument("--evaluate", action="store_true",
                      help="Evaluate the existing trained model.")

    parser.add_argument("--epochs", type=int, default=10,
                        help="Number of training epochs (default: 10).")
    parser.add_argument("--n-per-class", type=int, default=200,
                        help="Synthetic images per class (default: 200).")
    parser.add_argument("--batch-size", type=int, default=32,
                        help="Batch size (default: 32).")
    parser.add_argument("--learning-rate", type=float, default=1e-3,
                        help="Learning rate (default: 0.001).")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default: 42).")
    parser.add_argument("--classes", type=str, default=None,
                        help="Comma-separated class names (synthetic mode only). "
                             "Defaults to built-in disease classes.")
    parser.add_argument("--val-split", type=float, default=0.2,
                        help="Validation split fraction for real-image training (default: 0.2).")
    parser.add_argument("--verbose", action="store_true",
                        help="Verbose output.")

    args = parser.parse_args()

    # Parse classes for synthetic mode
    if args.classes:
        args.classes = [c.strip() for c in args.classes.split(",")]

    # Default: if no mode specified, use synthetic
    if not args.synthetic and not args.data_dir and not args.evaluate:
        args.synthetic = True

    if args.evaluate:
        result = evaluate(args)
    elif args.data_dir:
        result = train_real(args)
    else:
        result = train_synthetic(args)

    print(f"\n{'=' * 60}")
    print(f"Result: {json.dumps(result, indent=2)}")
    print(f"{'=' * 60}")

    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
