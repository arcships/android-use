"""Bounded, reproducible gesture sampling. No I/O or global random state."""

import random


class Variation:
    def __init__(self, options):
        self.options = options
        self.random = random.Random(options.get("seed"))
        self.audit = {}

    def delay(self):
        self.audit = {
            "seed": self.options.get("seed"),
            "delay_seconds": self.random.uniform(0, self.options["delay_max_seconds"]),
            "spatial_applied": False,
        }
        return self.audit["delay_seconds"]

    def duration(self, value, bounds, name="duration"):
        fraction = self.options["duration_fraction"]
        value = max(
            bounds[0], min(bounds[1], value * (1 + self.random.uniform(-fraction, fraction)))
        )
        self.audit[name] = value
        return value

    def point(self, point, rect, size):
        x, y, width, height = rect
        left, top = max(0, x), max(0, y)
        right, bottom = min(size[0], x + width) - 1, min(size[1], y + height) - 1
        # Stay in the interior; tiny controls reduce to a single valid point.
        inset_x, inset_y = min(2, (right - left) // 2), min(2, (bottom - top) // 2)
        left, right = left + inset_x, right - inset_x
        top, bottom = top + inset_y, bottom - inset_y
        radius = self.options["point_radius"]
        px, py = point
        low_x, high_x = max(left, px - radius), min(right, px + radius)
        low_y, high_y = max(top, py - radius), min(bottom, py + radius)
        # Explicitly retain the original point if an unusual/tiny rect cannot vary safely.
        if low_x > high_x or low_y > high_y:
            return tuple(point)
        changed = (self.random.randint(low_x, high_x), self.random.randint(low_y, high_y))
        self.audit["spatial_applied"] |= changed != tuple(point)
        return changed

    def swipe(self, start, end, rect, size, direction):
        sampled = self.point(start, rect, size), self.point(end, rect, size)
        a, b = sampled
        valid = {"up": a[1] > b[1], "down": a[1] < b[1], "left": a[0] > b[0], "right": a[0] < b[0]}[
            direction
        ]
        if not valid:
            self.audit["spatial_applied"] = False
            return start, end
        return sampled
