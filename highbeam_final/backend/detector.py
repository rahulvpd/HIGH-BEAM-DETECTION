"""
detector.py — AI High Beam Detection Engine
GradientBoosting classifier + 3-layer false positive filter

FIX: Thresholds are now instance variables (runtime-settable via set_threshold())
     so dashboard threshold controls actually take effect.
"""

import time
from datetime import datetime
from collections import deque

# Module-level defaults (used only at init time)
PERMANENT_HIGH_BEAM_THRESHOLD = 740
DEFAULT_HIGH_BEAM_THRESHOLD   = PERMANENT_HIGH_BEAM_THRESHOLD
DEFAULT_NORMAL_BEAM_THRESHOLD = 300
WINDOW_SIZE                   = 10
CONFIDENCE_THRESHOLD          = 0.85
NIGHT_START_HOUR              = 19
NIGHT_END_HOUR                = 6


class HighBeamDetector:
    def __init__(self):
        # FIX: Thresholds are instance variables so they can be changed at runtime
        self.high_beam_threshold   = DEFAULT_HIGH_BEAM_THRESHOLD
        self.normal_beam_threshold = DEFAULT_NORMAL_BEAM_THRESHOLD

        self.lux_window           = deque(maxlen=WINDOW_SIZE)
        self.false_positive_count = 0
        self.true_positive_count  = 0
        self._init_classifier()

    # ── FIX: Runtime threshold setter ────────────────────────────
    def set_threshold(self, high: int, normal=None):
        """Update thresholds at runtime — called by dashboard API."""
        self.high_beam_threshold = PERMANENT_HIGH_BEAM_THRESHOLD
        if normal is not None:
            self.normal_beam_threshold = max(50, min(self.high_beam_threshold - 50, int(normal)))
        # Re-train classifier with new thresholds
        self._init_classifier()
        print(f"[AI] Thresholds updated -> HIGH={self.high_beam_threshold} | NORMAL={self.normal_beam_threshold}")

    def _init_classifier(self):
        hi = self.high_beam_threshold
        lo = self.normal_beam_threshold
        try:
            from sklearn.ensemble import GradientBoostingClassifier
            from sklearn.preprocessing import StandardScaler
            import numpy as np

            rows, labels = [], []

            # Class 0: NOT violations
            for lux in [100, 150, 180, 200, 250, lo, int(hi*0.7), int(hi*0.8), int(hi*0.9), hi]:
                rows.append([lux, 0, 1, 0]);         labels.append(0)
                rows.append([lux, 0, 0, 0]);         labels.append(0)
                rows.append([lux, 0, 1, 2000]);      labels.append(0)
            for lux in [100, 150, 200, 250, lo, int(hi*0.5), int(hi*0.6)]:
                rows.append([lux, 1, 1, 500]);       labels.append(0)
                rows.append([lux, 1, 0, 500]);       labels.append(0)
            for lux in [hi, int(hi*1.1), int(hi*1.2), int(hi*1.3)]:
                rows.append([lux, 1, 0, 2500]);      labels.append(0)
                rows.append([lux, 1, 0, 3000]);      labels.append(0)
            for lux in [int(hi*1.03), int(hi*1.11), int(hi*1.17), int(hi*1.23)]:
                rows.append([lux, 1, 1, 500]);       labels.append(0)
                rows.append([lux, 1, 1, 1000]);      labels.append(0)

            # Class 1: Real violations (vehicle + high lux + sustained)
            for lux in [hi, int(hi*1.03), int(hi*1.07), int(hi*1.11),
                        int(hi*1.17), int(hi*1.26), int(hi*1.29), int(hi*1.43)]:
                rows.append([lux, 1, 1, 2000]);      labels.append(1)
                rows.append([lux, 1, 1, 2500]);      labels.append(1)
                rows.append([lux, 1, 1, 3000]);      labels.append(1)
                rows.append([lux, 1, 1, 3500]);      labels.append(1)

            X = np.array(rows, dtype=float)
            self.scaler = StandardScaler()
            Xs = self.scaler.fit_transform(X)
            self.classifier = GradientBoostingClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.1, random_state=42
            )
            self.classifier.fit(Xs, labels)
            self.ml_available = True
            print(f"[AI] GradientBoosting trained [OK] threshold={hi}")

        except ImportError:
            self.ml_available = False
            print("[AI] sklearn not available — threshold mode")

    def _moving_average(self, lux):
        self.lux_window.append(lux)
        return sum(self.lux_window) / len(self.lux_window)

    def _is_night(self):
        h = datetime.now().hour
        return h >= NIGHT_START_HOUR or h < NIGHT_END_HOUR

    def _confidence(self, lux, vehicle, duration_ms):
        hi = self.high_beam_threshold
        if not self.ml_available:
            return 0.92 if (lux > hi and vehicle and duration_ms > 2000) else 0.3
        import numpy as np
        f = np.array([[lux, int(vehicle), 1, duration_ms]])
        return float(self.classifier.predict_proba(self.scaler.transform(f))[0][1])

    def analyze(self, data: dict) -> dict:
        lux     = data.get("lux", 0)
        vehicle = data.get("vehicle_present", False)
        dur     = data.get("duration_ms", 0)
        event   = data.get("event", "SENSOR")

        hi = self.high_beam_threshold
        lo = self.normal_beam_threshold

        # Layer 1: vehicle present?
        if not vehicle:
            return self._r("CLEAR", lux, 0.0, "L1_NO_VEHICLE")

        # Pre-fill window
        if len(self.lux_window) == 0:
            for _ in range(WINDOW_SIZE): self.lux_window.append(lux)
        avg = self._moving_average(lux)

        # Hard safety rule: a vehicle with current LDR lux below the high-beam
        # threshold is never a fine, even if earlier readings kept the moving
        # average high.
        if lux <= hi:
            if lux > lo:
                return self._r("WARNING", lux, 0.0, "L2_CURRENT_LUX_BELOW_FINE_THRESHOLD")
            return self._r("NORMAL", lux, 0.0, "L2_CURRENT_LUX_LOW")

        # Current sustained high lux with a vehicle is a violation. Use the
        # current LDR value, not an older moving average, for fine eligibility.
        if lux > hi and dur >= 2000:
            conf = self._confidence(lux, vehicle, dur)
            if conf >= CONFIDENCE_THRESHOLD:
                self.true_positive_count += 1
                return self._r("VIOLATION", lux, conf, "L3_CURRENT_LUX_CONFIRMED")
            self.false_positive_count += 1
            return self._r("FALSE_POSITIVE", lux, conf, "L3_AI_REJECTED")

        # Layer 2: lux check against current threshold
        if avg <= hi:
            if avg > lo:
                return self._r("WARNING", lux, 0.0, "L2_BORDERLINE")
            return self._r("NORMAL", lux, 0.0, "L2_LUX_LOW")

        # Layer 3: AI confidence
        conf = self._confidence(avg, vehicle, dur)
        if conf >= CONFIDENCE_THRESHOLD:
            self.true_positive_count += 1
            return self._r("VIOLATION", lux, conf, "L3_AI_CONFIRMED")
        self.false_positive_count += 1
        return self._r("FALSE_POSITIVE", lux, conf, "L3_AI_REJECTED")

    def _r(self, decision, lux, conf, reason):
        return {
            "decision":   decision,
            "lux":        lux,
            "confidence": round(conf, 3),
            "reason":     reason,
            "timestamp":  datetime.now().isoformat(),
            "avg_lux":    round(sum(self.lux_window) / max(len(self.lux_window), 1), 1),
            "threshold":  self.high_beam_threshold,
        }

    def stats(self):
        total = self.true_positive_count + self.false_positive_count
        return {
            "model":                 "GradientBoosting" if self.ml_available else "Threshold",
            "true_positives":        self.true_positive_count,
            "false_positives":       self.false_positive_count,
            "accuracy":              100.0 if total == 0 else round(self.true_positive_count / total * 100, 1),
            "high_beam_threshold":   self.high_beam_threshold,
            "normal_beam_threshold": self.normal_beam_threshold,
        }
