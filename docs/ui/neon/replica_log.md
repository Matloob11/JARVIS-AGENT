# Overview replica: visual diff log

Target: `JARVIS & ANNA Neon Command Center.png` (1672x941). Replica: `overview_replica.html`, screenshot with Playwright Chromium at exactly 1672x941, deviceScaleFactor 1, `?still=1` (the deterministic rest frame of the animated page).

**Metrics** (all on the full 1672x941 frame unless a region is named):

- **MAE**: mean absolute RGB error per channel, 0-255. **RMSE / PSNR** from the same difference.
- **SSIM**: luminance SSIM, Gaussian window sigma 1.5, K1 .01, K2 .03 (Wang et al. 2004). scikit-image is not installed in this environment, so it is computed with the same formula in numpy/scipy (`diff.py: ssim_map`).
- **dE76**: mean per-pixel CIE76 colour difference in Lab; also the share of pixels with dE < 5 and < 10. Each region also reports the CIEDE2000 difference of its mean colour.
- **Ink boxes**: for 96 text lines and icons, the tight ink box is measured with the same method in both images (`diff.py: ink_box`); the error is the worst edge displacement in px.
- **Container edges**: left/right/top/bottom border positions of 26 cards, buttons, pills, bubbles and inputs (max gradient on scanlines), in px.

## Iterations

| Iter | MAE | SSIM | PSNR | dE76 | px dE<5 | ink <=1px / <=2px / max | edges <=1px / max | orb MAE | hero_text MAE | What changed |
|---|---|---|---|---|---|---|---|---|---|---|
| it00 | 6.55 | 0.9035 | 21.62 | 5.31 | 77.4% | 70 / 83 / 6 | 98 / 3 | 14.04 | 11.23 | First full build. Text placed from Chrome-measured ink offsets (calib2.py: per-element size/tracking so ink width and cap height match), Lucide-style inline SVG icons, hand-built canvas orb. |
| it01 | 6.38 | 0.9034 | 21.70 | 5.17 | 78.2% | 70 / 83 / 6 | 98 / 3 | 12.01 | 11.45 | Orb rewritten as a parametric module (body gradient, light/dark lobes split by the streak axis, streak, core, rim, rings, particle halo from detected reference particles). First guess, not fitted. |
| it02 | 5.86 | 0.9089 | 22.14 | 4.62 | 79.7% | 70 / 83 / 6 | 98 / 3 | 6.93 | 11.23 | Orb parameters fitted against the reference by coordinate descent in the live page (orbfit.py, 7 passes, loss 22.9 -> 11.4). |
| it03 | 6.26 | 0.9092 | 21.96 | 4.84 | 78.5% | 70 / 83 / 6 | 98 / 3 | 11.06 | 11.23 | Orb switched to additive light (canvas mix-blend-mode: plus-lighter) with 7 measured rings (fitted circle, centre offset, FWHM, light per 5 degrees). Before refitting, so the orb got worse. |
| it04 | 5.75 | 0.9123 | 22.44 | 4.59 | 79.3% | 87 / 93 / 5 | 98 / 3 | 7.64 | 10.91 | Orb refit (6 passes, 22.8 -> 11.1). Icons calibrated to the reference ink boxes (2 rounds). Hero and confirm button content placed absolutely. Text colours matched to reference glyph cores. Code line 2 tracking. |
| it05 | 5.47 | 0.9139 | 22.58 | 4.27 | 82.8% | 88 / 94 / 4 | 98 / 3 | 6.96 | 11.24 | Page, sidebar and hero backgrounds fitted as CSS radial/linear gradients (bgfit.py, numpy model of CSS gradients, L-BFGS-B on 7000 masked pixels). |
| it06 | 5.70 | 0.9124 | 22.60 | 4.27 | 83.2% | 88 / 94 / 4 | 98 / 3 | 6.63 | 11.12 | Tried opacity:.999 on the stage to get grayscale text antialiasing like the reference: the extra layer truncated every colour by 1 level. Rejected. |
| it07 | 5.29 | 0.9144 | 22.87 | 3.85 | 83.9% | 88 / 94 / 4 | 98 / 3 | 6.53 | 11.24 | will-change:transform on the stage instead: grayscale text AA without colour shift (it07b, filter variant, was worse). Accepted. |
| it08 | 5.29 | 0.9144 | 22.87 | 3.85 | 83.9% | 88 / 94 / 4 | 98 / 3 | 6.53 | 11.24 | will-change only at 1:1 scale so scaled views re-raster text crisply. Same pixels. |
| it09 | 5.29 | 0.9144 | 22.88 | 3.85 | 83.9% | 88 / 94 / 4 | 98 / 3 | 6.51 | 11.24 | Orb refit with the new hero background (5 passes, 11.6 -> 10.7). |
| it10 | 5.27 | 0.9167 | 22.88 | 3.84 | 84.0% | 88 / 94 / 4 | 98 / 3 | 6.31 | 11.24 | Measured swirl ribbons added inside the glass: 7 ellipses found by a constrained Hough search on the ridge map (swirlfind.py), light profile along each ellipse. |
| it11 | 5.16 | 0.9171 | 22.91 | 3.72 | 85.5% | 88 / 94 / 4 | 98 / 3 | 6.30 | 11.24 | Sidebar silk wave redrawn from the measured crest curves. |
| it12 | 5.17 | 0.9171 | 22.90 | 3.71 | 85.5% | 88 / 94 / 4 | 98 / 3 | 6.29 | 11.24 | Wave made parametric (SVG generated from WAVE_P). |
| it13 | 5.07 | 0.9181 | 22.93 | 3.63 | 86.5% | 88 / 94 / 4 | 98 / 3 | 6.28 | 11.24 | Wave fitted (wavefit.py, 5 passes, 4.67 -> 1.78). |
| it14 | 5.05 | 0.9186 | 22.96 | 3.62 | 86.5% | 88 / 94 / 4 | 98 / 3 | 6.26 | 10.91 | Waveform bars re-measured on the reference (30 bars: 2.6px wide, core colours, exact centres and lengths). |
| it15 | 5.06 | 0.9185 | 22.95 | 3.63 | 86.4% | 88 / 94 / 4 | 98 / 3 | 6.26 | 11.07 | Tried a canvas glow on the waveform bars: worse in the text column, reverted. Orb refit with swirl gain (5 passes). |
| it16 | 5.05 | 0.9187 | 22.96 | 3.62 | 86.5% | 88 / 94 / 4 | 98 / 3 | 6.25 | 10.91 | Swirl layer drawn without seams (source-over inside the layer, additive onto the orb) plus a soft glow copy; swirl params refit. |
| it17 | 5.02 | 0.9214 | 22.98 | 3.61 | 86.5% | 89 / 94 / 4 | 101 / 3 | 6.19 | 10.91 | Soft outer half of every hairline border (box-shadow rows) and 2px borders where the reference draws them (pills, avatar, Deny); avatar circle moved to its measured 60px box. |
| it18 | 5.01 | 0.9215 | 22.98 | 3.60 | 86.5% | 89 / 94 / 4 | 101 / 3 | 6.17 | 10.91 | Orb refit with an SSIM term in the loss (structure, not only colour): 5 passes. |
| it19 | 5.16 | 0.9201 | 22.90 | 3.67 | 86.2% | 89 / 94 / 4 | 101 / 3 | 7.81 | 10.68 | Hero background refit against (reference minus the orb light read back from the canvas), with two extra vignette layers (herofit.py). Text column improved; orb worse until refit. |
| it20 | 5.14 | 0.9203 | 22.91 | 3.66 | 86.4% | 89 / 94 / 4 | 101 / 3 | 7.81 | 10.68 | Start Listening glow toned down and shifted left, light bottom rim on the button. |
| it21 | 5.00 | 0.9218 | 22.97 | 3.62 | 86.7% | 89 / 94 / 4 | 101 / 3 | 6.43 | 10.67 | Hero corner light arcs and sparkle moved to their measured positions (ridge map). |
| it22 | 5.00 | 0.9217 | 22.97 | 3.63 | 86.7% | 88 / 94 / 4 | 101 / 3 | 6.43 | 10.67 | Users, activity and settings icons redrawn closer to the reference shapes (8-lobe cog), recalibrated. |
| it23 | 4.93 | 0.9246 | 23.05 | 3.57 | 86.8% | 88 / 94 / 4 | 101 / 3 | 5.81 | 10.67 | Half-pixel fix: the reference measurements are pixel-centre coordinates, CSS/canvas coordinates are pixel edges. Orb and logo centres moved by +0.5 px. Orb MAE 6.17 -> 5.81, orb SSIM .888 -> .914. |
| it24 | 4.93 | 0.9248 | 23.05 | 3.57 | 86.8% | 88 / 94 / 4 | 101 / 3 | 5.80 | 10.67 | Halo particles re-detected with their added light (peak minus local background) instead of absolute colour, 215 dots. (Orb columns also include the passes of the post-fix orb refit that were saved while this ran.) |
| it25 | 4.90 | 0.9254 | 23.08 | 3.53 | 87.0% | 88 / 94 / 4 | 101 / 3 | 5.57 | 10.67 | Sidebar logo ring redrawn from its measured bands (glossy inner tube, deeper outer band, lit edge, 12 angle samples each); timeline dots 14 px. (Orb columns also include the passes of the post-fix orb refit that were saved while this ran.) |
| it26 | 4.89 | 0.9256 | 23.09 | 3.53 | 87.0% | 88 / 94 / 4 | 101 / 3 | 5.57 | 10.67 | Logo ring radii and shading refined against its polar profile. (Orb columns also include the passes of the post-fix orb refit that were saved while this ran.) |
| it27 | 4.87 | 0.9260 | 23.11 | 3.51 | 87.1% | 88 / 94 / 4 | 101 / 3 | 5.47 | 10.67 | JARVIS chat avatar rebuilt to measured geometry: small brand ring on a near-black disc, 2 px blue ring at r 21.7 with glow. (Orb columns also include the passes of the post-fix orb refit that were saved while this ran.) |
| it28 | 5.03 | 0.9259 | 23.04 | 3.61 | 86.3% | 88 / 94 / 4 | 101 / 3 | 7.14 | 10.66 | Orb refit finished (6 passes, 13.9 -> 12.4, with 3 extra lobes). Removed the hero background layer the fit had placed under the glass (a dark ring hidden by the sphere that would show through when the orb dims); orb brighter until refit. |
| it29 | 4.84 | 0.9265 | 23.11 | 3.48 | 87.3% | 88 / 94 / 4 | 101 / 3 | 5.24 | 10.66 | Final orb refit over the cleaned hero background (6 passes, 17.5 -> 12.1). Orb MAE 5.24, orb SSIM .924. Final: mode Mic Off one-way, idle animation throttled to ~30 fps, orb click limited to the sphere. |

(Ink boxes are out of 96 measured; container edges out of 104.)

## Final iteration (it29), per region

| Region | MAE | SSIM | Mean colour ref | Mean colour replica | dE2000 of means |
|---|---|---|---|---|---|
| sidebar | 2.85 | 0.962 | `#0f162e` | `#0f172e` | 0.3 |
| header | 5.85 | 0.919 | `#121b37` | `#131d38` | 0.37 |
| hero | 5.51 | 0.920 | `#172450` | `#172450` | 0.12 |
| orb | 5.24 | 0.924 | `#172660` | `#17255f` | 0.21 |
| hero_text | 10.66 | 0.832 | `#202841` | `#222a41` | 0.5 |
| hero_buttons | 6.42 | 0.907 | `#1f346f` | `#213671` | 0.42 |
| quickstats | 4.76 | 0.930 | `#182236` | `#192237` | 0.42 |
| activity | 7.71 | 0.868 | `#151d31` | `#161e32` | 0.35 |
| panel | 5.06 | 0.917 | `#171f36` | `#171f37` | 0.47 |
| panel_tabs | 5.98 | 0.897 | `#151d36` | `#151e37` | 0.29 |
| confirm | 6.62 | 0.902 | `#1b233b` | `#1c243c` | 0.68 |
| chat | 5.94 | 0.910 | `#192239` | `#192239` | 0.31 |
| composer | 3.58 | 0.925 | `#15203b` | `#15203b` | 0.21 |
| page_bg_gaps | 1.21 | 0.986 | `#091127` | `#081026` | - |

## Final iteration, worst ink boxes

| Element | Error px | Reference box (x, y, w, h) | Replica box | Note |
|---|---|---|---|---|
| panel.tab.approvals:text | 4 | (1346, 145, 75, 16) | (1346, 141, 75, 17) | normalised on purpose: the reference draws "Approvals" 3-4 px below "Chat"/"Tools"; the replica keeps one baseline. |
| activity.item1:subtitle | 3 | (784, 712, 147, 14) | (784, 712, 144, 13) | reference draws the four subtitles at slightly different sizes; the replica uses one size for the list |
| header.pill.devices:icon | 2 | (960, 47, 23, 18) | (960, 47, 21, 19) |  |
| hero.btn.micoff:icon | 2 | (888, 492, 21, 24) | (888, 492, 23, 23) |  |
| activity.item2:subtitle | 2 | (785, 766, 136, 13) | (785, 765, 138, 13) | reference draws the four subtitles at slightly different sizes; the replica uses one size for the list |
| activity.item4:subtitle | 2 | (784, 874, 138, 14) | (784, 873, 140, 13) | reference draws the four subtitles at slightly different sizes; the replica uses one size for the list |
| panel.chat.jarvis.bubble:line2 | 2 | (1249, 674, 300, 16) | (1249, 674, 298, 16) |  |
| panel.chat.user.avatar:text | 1.5 | (1187, 525, 12, 14) | (1187, 527, 15, 13) | centre compared: "AM"/"A" replaced by "M" for Matloob |
| sidebar.brand_title:text | 1 | (55, 103, 164, 15) | (55, 103, 165, 15) |  |
| sidebar.brand_subtitle:text | 1 | (53, 131, 168, 11) | (53, 131, 169, 11) |  |
| sidebar.nav.overview:text | 1 | (93, 210, 74, 13) | (92, 210, 75, 14) |  |
| sidebar.nav.overview:icon | 1 | (42, 204, 24, 24) | (42, 204, 25, 23) |  |
| sidebar.nav.agents:icon | 1 | (41, 272, 26, 20) | (41, 272, 25, 21) |  |
| sidebar.nav.activity:icon | 1 | (42, 336, 24, 19) | (42, 336, 24, 20) |  |

Container edges off by more than 1 px in the final iteration: hero {'l': 0.0, 'r': 0.0, 't': 2.0, 'b': 0.0}, deny {'l': -1.0, 'r': 0.0, 't': -1.0, 'b': 2.0}, send {'l': -1.0, 'r': 0.0, 't': 3.0, 'b': 1.0}.

The three flagged container edges are detector noise rather than offsets: the row profiles across those edges put the borders on the same rows in both images (the reference spreads each hairline over two rows, so the edge detector can pick a neighbouring row).

## Where the remaining error is

- **62%** of the remaining error sits inside the text and icon boxes, which cover 12% of the frame (MAE 24.1 there). The reference is an AI render: its glyphs are softer, slightly differently shaped and carry dark sharpening halos, while Chrome draws Segoe UI crisply. Positions and widths match (see the ink boxes); the glyph pixels themselves cannot.
- **10%** is in the orb (9% of the frame): its rings, rim, particles and ribbons sit at the measured places, but the fine filament texture of the painted orb is not reproduced pixel for pixel.
- Everything else (79% of the frame: backgrounds, cards, borders, buttons, bars) has an MAE of **1.74**.

## Deliberate differences from the reference

- "Alex", "AM" and "A" are "Matloob" and "M" (same boxes and styles).
- The "Approvals" tab label sits on the same baseline as "Chat" and "Tools" (the reference draws it 3-4 px low).
- The progress bars are bound to their numbers: CPU 21% fills 21% of the track (the reference draws about 26%), Disk 82% fills 82% (reference about 79%). Memory and devices match.
- The 1 px dark halos around bright text and borders (image sharpening) are not reproduced.
- In live mode (without `?still=1`) the greeting follows the local time ("Good morning / afternoon / evening") and CPU and memory drift like live vitals; the still frame shows the reference values.

## Heatmap

`replica_diff.png` is the per-pixel mean absolute RGB difference of the final iteration, multiplied by 4 and mapped with the inferno colour map (black = identical, yellow = 64 or more levels apart).

## How the replica was matched

- **Text:** each line was rendered in Chrome at candidate sizes; the size and tracking that reproduce the reference ink width and cap height were kept (calib2.py). Positions come from the measured baseline offsets of each font (build.py writes them as plain CSS rules).
- **Icons:** inline SVG (Lucide-style geometry), each scaled and placed so its ink box equals the reference ink box (iconfit.py).
- **Backgrounds:** page, sidebar and hero gradients fitted in a numpy model of CSS radial and linear gradients (bgfit.py, herofit.py). The sidebar wave is an SVG whose curves and opacities were fitted in the page (wavefit.py).
- **Orb:** one canvas with `mix-blend-mode: plus-lighter`, so its light adds to the card. Layers: glow, outer light lobes, 215 halo particles at their measured positions, 7 measured rings (circle fit, centre offset, width, light every 5 degrees), the glass sphere (radial body gradient, 9 light and dark lobes split by the streak axis, streak glow, inner rim light), 7 measured orbit ribbons (constrained Hough search on the ridge map), streak, core and a conic rim. About 240 numbers were fitted by coordinate descent against the reference in the live page (orbfit.py; loss = blurred MAE + plain MAE + SSIM term).
- **Motion:** idle breathing (5.2 s, +-1.2%) at about 30 fps. While speaking or listening, a level L (simulated speech envelope, or the microphone through Web Audio) drives scale `1 + .06L`, jitter above L = .3, glow, ring light, ribbon drift, a wobbling rim and ripples on onsets. In the interaction test the orb changes about 8x more per frame while speaking than at rest; with `prefers-reduced-motion` it draws one still frame.

