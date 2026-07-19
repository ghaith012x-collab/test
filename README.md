# Rotate Captcha Solver

Offline solver for TikTok-style rotation captchas. It estimates the rotation
angle needed to align an inner (rotatable) image with an outer reference
circle using **edge continuity analysis** and **ORB feature matching**.

## How it works

1. Load outer and inner images from a path, bytes, `np.ndarray`, or `PIL.Image`.
2. Preprocess both into edge maps (resize → Gaussian blur → adaptive threshold → Canny).
3. Brute-force search rotation angles (default ±180° at 2° steps) and score each:
   - `_compute_edge_continuity_score` — measures how well inner edges continue outer edges.
   - `_feature_matching_score` — ORB descriptor matches between the two images.
4. Refine the best angle with a smaller step for precision.
5. Return `(angle, confidence)`.

## Install

```bash
pip install -r requirements.txt
```

## Usage

```python
from captcha_solver import solve_rotate_captcha, solve_rotate_captcha_robust

angle, confidence = solve_rotate_captcha('outer.png', 'inner.png', debug=True)
print(angle, confidence)

# More robust: tries multiple methods and returns the most confident result
angle, confidence = solve_rotate_captcha_robust('outer.png', 'inner.png')
```

### With Playwright

```python
from captcha_solver import solve_from_playwright_screenshots

angle, confidence = solve_from_playwright_screenshots(page)
```

## API

| Function | Returns | Description |
| --- | --- | --- |
| `solve_rotate_captcha(outer, inner, debug=False)` | `(angle, confidence)` | Standard solver. |
| `solve_rotate_captcha_robust(outer, inner, debug=False)` | `(angle, confidence)` | Tries several methods, returns best. |
| `solve_from_playwright_screenshots(page, ...)` | `(angle, confidence)` | Helper for Playwright pages. |

Inputs accept `str` (path), `bytes`, `np.ndarray`, or `PIL.Image`.
