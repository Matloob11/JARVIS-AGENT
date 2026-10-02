# JARVIS & ANNA Neon Command Center: visual forensics

Source: `JARVIS & ANNA Neon Command Center.png` (1672x941 RGB, no metadata). Everything below was measured from pixels with PIL/numpy/scipy/OpenCV and checked against Playwright renders. Machine-readable copy: `forensics.json` (73 element records, tokens, orb, waveform, font scores). Detail crops: `crops/` (index in section 13).

**Units.** All numbers are reference-image pixels. Boxes are `x,y wxh` with x,y the top-left pixel. The render is a 1:1 desktop frame: body text measures 16px and labels 12-14px, so treat 1 image px as 1 CSS px at a 1672x941 viewport. On 1920 wide multiply by 1.148; on 1440 by 0.861, or keep px and let the fluid columns take the width.

**Personalization.** The reference says "Good evening, Alex", avatar "AM" and chat avatar "A". Build it as **"Good evening, Matloob"**, header avatar **"M"** and chat avatar **"M"**, keeping the same boxes and styles.

**Rendering caveat.** The image is an AI render. Text spacing, a few alignments and the progress-bar fill lengths are slightly irregular (section 12 lists what to normalise). The 1px dark halos that hug bright text and borders are sharpening artefacts; don't reproduce them.

## 1. Key findings

- **Layout:** sidebar 271px plus a 1px divider at x=271. Main column 830px wide (hero 474px tall over two 306px cards). Right panel 512x800. Gaps are 15-18px, outer margins 17-28px.
- **Palette:** near-black navy canvas `#070e1f` with a blue bloom behind the header (`#0c183b`). Surfaces are 1-3 steps lighter (`#0a1123` to `#192139`). Borders are 1px cool blue-grey, lit from the top. The accent is a saturated azure-to-royal blue (`#3aa8fd` to `#2541f8`). Status colours: green `#22c086`, blue `#4a95fb`, violet `#7a50f9`, amber `#f9bc54`. The approval card is the only warm element (amber border `#3a2c29`, amber pill `#2e231e`/`#e6a64e`).
- **Type:** the UI text is **Segoe UI**. It is the best match of 28 sans families tested, scoring .726 on body text against .661 for the best Google font. The **brand words** ("JARVIS" hero, "JARVIS & ANNA" wordmark) are wide geometric caps that match **Montserrat 700/600** best. Code is **JetBrains Mono / Source Code Pro** at 15px (char advance 8.92px).
- **Radii:** 15 (hero, panel), 13-14 (cards, tiles, buttons, pills, bubbles), 11-12 (nav item, approve/deny, input, code). Proposed scale: 12 / 14 / 16 / full.
- **Depth:** no drop shadows on cards. Depth comes from 1px top-lit borders, faint vertical surface gradients, radial corner glows inside the hero, and coloured glows only on the primary hero button, the active tab underline, status dots, progress fills and the orb.
- **Orb:** centre (534,319), sphere radius 107 with a 3px rim. It has a dark-blue interior, a white core hotspot, a 22-degree lens streak, swirl ribbons, 4 thin orbit rings (r 113.5-134), a particle halo (r 140-230) and a soft glow that fades to background by r 185.

## 2. Layout grid

```
x:   0        271  295                                  1124 1143                       1654 1672
y0   +----------+  +--------------------- header band  y 0-114 ------------------------------+
     | sidebar  |  | Overview (x314)   [System Online][3 Connected Devices][Voice Ready] | (M) Good evening, Matloob v
y114 | logo     |  |                                         +-------------------------------+
y116 | brand    |  +---------------------------------------+ | RIGHT PANEL 512x800 (y114-913)|
     | nav x4   |  | HERO 830x474                          | |  tabs        y125-180         |
     |          |  |  orb (534,319)   text column x742     | |  confirm     476x268          |
     |          |  |  buttons y476-531   quote y558-567    | |  chat bubbles y499-730        |
y589 |          |  +---------------------------------------+ |                               |
y606 |          |  +-- QUICK STATS 411x306 -+ +- ACTIVITY 404x305 -+ |  composer 481x129    |
     | wave     |  | 2x2 tiles 180x91/95    | | 4 timeline items   | |                      |
y911 | tagline  |  +------------------------+ +--------------------+ +-------------y913-----+
y941 +----------+
```

| Region | Box (x,y wxh) | Notes |
|---|---|---|
| sidebar | 0,0 271x941 | radius - |
| hero | 295,116 830x474 | radius 15 |
| quickstats | 295,606 411x306 | radius 13 |
| activity | 721,607 404x305 | radius 13 |
| panel | 1143,114 512x800 | radius 15 |
| sidebar divider | 271,0 1x941 | `#1f2a48` (measured #1d2845..#222c4b) |
| header band | 272,0 1400x114 | content band y 29-82, centre y 55.5; title starts x 314 |

Measured gaps: sidebar divider to hero 23px; hero to right panel 18px; hero to lower cards 16px; Quick Stats to Activity 15px; right margin 17px; bottom margin 28-30px.

Normalised CSS proposal:
```css
.app      { display:grid; grid-template-columns: 272px 1fr; height:100vh; background:var(--bg); }
.main     { display:grid; grid-template-rows: 114px 1fr; padding: 0 18px 28px 24px; }
.content  { display:grid; grid-template-columns: minmax(0,1fr) 512px; column-gap:18px; }
.left     { display:grid; grid-template-rows: 474px 306px; row-gap:16px; }
.lower    { display:grid; grid-template-columns: 1fr 1fr; column-gap:16px; }
/* right panel spans both rows: height 800px, top aligned 2px above hero (normalise to same top) */
```

## 3. Colour tokens (sampled)

| Token | Hex | Where sampled |
|---|---|---|
| `bg.canvas` | `#070e1f` | page background bottom/right (1660,500), gaps between cards |
| `bg.canvas_top_bloom` | `#0c183b` | behind header/hero top (700..760, 92..110); peak #0d1737 at (1000,10) |
| `bg.sidebar` | `#0a1124` | sidebar body (100..260, 300..600) |
| `bg.sidebar_top` | `#0f1631` | sidebar top-left corner (5,5) |
| `border.sidebar_divider` | `#1f2a48` | x=271 (1px; #1d2845..#222c4b) |
| `surface.hero` | `#0c1329` | hero card body (950,330) |
| `surface.card` | `#0d152b` | quick stats / activity top; bottom #0a1325 |
| `surface.panel` | `#0a1123` | right panel body; top #10172f |
| `surface.tile` | `#151e34` | stat tiles |
| `surface.raised` | `#192139` | user chat bubble |
| `surface.raised_2` | `#121b32` | assistant chat bubble |
| `surface.input` | `#15203d` | composer input |
| `surface.composer` | `#0e182c` | composer container (top #121b32) |
| `surface.code` | `#060c1c` | PowerShell code block |
| `surface.ghost_button` | `#121b34` | Run Task / Deny (Mic Off #18213b) |
| `surface.pill` | `#132145` | header pills (system #15233f, voice #111f43) |
| `surface.nav_active` | `#16285b -> #172141` | active nav item, left -> right |
| `surface.warning_card` | `#151826` | Confirm Command card |
| `surface.warning_pill` | `#2e231e` | Requires approval pill |
| `surface.avatar_user` | `#36405c` | chat user avatar circle |
| `border.card` | `#121e37` | quick stats / activity border (top); bottom #0f1a32 (~rgba(110,140,220,.10)) |
| `border.panel_top` | `#232d47` | right panel top border; sides #18223a; bottom #111a30 |
| `border.hero_corner` | `#2f3b81` | hero top-left border; top-right #25498d; mid #202b4b; bottom #172648 |
| `border.tile` | `#1e2a43` | tile border |
| `border.control` | `#2e364f` | Deny / Mic Off border (Run Task #232e4c) |
| `border.input` | `#1b2744` | input border; composer top #23304e |
| `border.code` | `#151d2f` | code block border |
| `border.warning` | `#3a2c29` | confirm card border (#362a2a..#3f302a) |
| `border.warning_pill` | `#523a26` | requires-approval pill border |
| `divider.tabs` | `#172035` | y=179..180 across panel |
| `divider.header` | `#354369` | x=1366, y 40..71 |
| `text.strong` | `#ffffff` | titles, values, active labels, button labels |
| `text.pill` | `#f2f5fa` | header pill labels (top-4% #f7f9fd) |
| `text.chat_user` | `#ced8ee` | user bubble text |
| `text.chat_assistant` | `#b0bcdb` | assistant bubble text |
| `text.secondary` | `#a7b1d3` | nav labels #a5b0dc, header subtitle #abb6de, greeting #a7b0d2, confirm question #b4bfd9 |
| `text.muted` | `#97a3c2` | card subtitles #a0acca/#9baacb, item subs #94a1bf, tile labels #97a2c0, hints #96a3be |
| `text.dim` | `#7e8db4` | timestamps (#7e8db4 activity, #7186b4 user bubble, #5d739f assistant bubble), sidebar subtitle #7e89ac |
| `text.faint` | `#5c6a94` | sidebar footer tagline |
| `text.hero_role` | `#8192cc` | AI Assistant |
| `text.hero_eyebrow` | `#92a3d4` | JARVIS & ANNA eyebrow |
| `text.hero_quote` | `#95aadf` | quote |
| `text.hero_title` | `#e4eafc` | JARVIS (core #dde4fb) |
| `accent.blue` | `#3b82f6` | family of #2f75fc..#4ba8ff (buttons, underline, accents) |
| `accent.blue_light` | `#8dc4ff` | Listening for your command |
| `accent.blue_icon` | `#63aafb` | active nav icon / header icons #66b8fc |
| `accent.cyan_underline` | `#4ba8ff` | active tab underline |
| `accent.rule` | `#6290f2` | short rule under AI Assistant |
| `accent.jarvis_label` | `#acc8f9` | JARVIS label in assistant bubble |
| `accent.badge` | `#3a5fd2` | Approvals count badge |
| `accent.send` | `#163b80` | send button fill |
| `status.green` | `#22c086` | System Online dot (#2acc91 core), timeline dot 1 #23b084, CPU bar #35b992, chip icon #4ce0bf |
| `status.blue` | `#4a95fb` | timeline dot 2, memory bar #3590fc..#4898f9 |
| `status.violet` | `#7a50f9` | timeline dot 3; devices bar #8981f6; laptop icon #bfbdf9 |
| `status.gray` | `#8895bc` | timeline dot 4 |
| `status.amber` | `#f9bc54` | disk bar; disk icon #fbc551; shield #f9c74c; requires-approval text #e6a64e, sparkle #f1a74d |
| `track` | `#222b46` | progress track |
| `timeline.line` | `#2b3552` | x=751 connector (1px) |

Per-text colours (median of the brightest 4% of glyph pixels, the closest estimate of the true fill) are in `forensics.json > text_colors_measured`.

## 4. Surfaces, borders and gradients (CSS recipes)

- **page_bg:** `radial-gradient(900px 260px at 47% 0%, rgba(30,62,150,.35), transparent 70%), radial-gradient(600px 300px at 0% 75%, rgba(30,60,170,.18), transparent 70%), #070e1f`
- **sidebar_bg:** `linear-gradient(180deg,#0f1631 0%,#0b1326 12%,#0a1124 22%,#0a1124 100%) + wave decoration`
- **hero_card:** `#0c1329 + radial TL rgba(80,70,255,.20) 150px + radial TR rgba(40,110,255,.35) at (94%,11%) 120px + radial BL rgba(40,150,255,.15) at (0%,70%) 90px`
- **hero_border:** `linear-gradient(180deg, rgba(110,140,255,.38) 0%, rgba(110,140,255,.16) 30%, rgba(110,140,255,.12) 100%) as 1px border-box layer (corners brighter)`
- **btn_start:** `linear-gradient(115deg,#3aa8fd 0%,#3189fe 22%,#2d66fc 48%,#2645f8 78%,#2a5ffb 100%)`
- **btn_approve:** `radial-gradient(120% 140% at 50% 50%, #245afb 45%, #2f78fd 100%)`
- **nav_active:** `linear-gradient(90deg,#16285b 0%,#16244b 25%,#172141 70%)`
- **panel:** `linear-gradient(180deg,#10172f 0%,#0b1224 17%,#0a1123 40%)`
- **cards:** `linear-gradient(180deg,#0e152d 0%,#0a1325 100%)`
- **composer:** `linear-gradient(180deg,#121b32 0%,#0e172c 100%)`
- **avatar:** `linear-gradient(160deg,#0f2a70 0%,#0d1838 100%)`
- **waveform:** `linear-gradient(90deg, #3948a4 0%, #6880f8 22%, #53ccf9 50%, #4682eb 75%, rgba(19,32,73,0) 100%) applied per bar (alpha fades at both ends)`
- **logo_ring_css_conic:** `conic-gradient(from 0deg, #4775f1 0deg, #80b1fd 30deg, #7d90fc 60deg, #876bfc 90deg, #d089f9 120deg, #ebbdff 150deg, #906afb 180deg, #554bf0 210deg, #4c4ced 240deg, #303fdc 270deg, #3056e5 300deg, #3f72ef 330deg, #4775f1 360deg)`

Border pattern: every container border is 1px and **lit from the top**. Top is about 1.3x brighter than the bottom. Examples: panel top `#232d47`, sides `#18223a`, bottom `#111a30`; composer top `#23304e`, bottom `#131f36`. The hero border is brightest at its two top corners (`#384694` TL, `#2552a4` TR). The System Online pill border picks up a teal tint near its dot (`#21405a`).

Hero card interior (opening-filtered background map, see `crops/30_hero_enhanced.png`):
- base `#0b1126`..`#0d142a`
- top-left violet bloom `#161e57` at (305,124), gone by ~150px
- top-right blue flare `#173c82` peak at ~(1085,165), radius ~120, with a 2px sparkle at (1082,177) and faint 1px light arcs sweeping down-left toward (960,390)
- bottom-left cyan glow on the left edge, y 420-470
- orb glow (section 10)

Sidebar: `#0a1124`, lighter at top-left (`#0f1631`). Silk-ribbon waves in the lower-left (section 11).

## 5. Glows and shadows

- **cards:** no drop shadow (only 1px borders; a 1px darker ring hugs some borders = render sharpening, optional)
- **btn_primary_hero:** 0 6px 22px rgba(37,90,255,.45) - glow measurable ~20px around Start Listening (#0d1f5f just below vs #0b132b)
- **btn_approve:** none outside; inner edge glow only
- **tab_underline:** 0 0 10px 1px rgba(75,168,255,.45) (visible y 170..186)
- **status_dot:** 0 0 8px rgba(34,197,134,.45)
- **timeline_dots:** blue/violet: 0 0 8px currentColor @ .5; green faint; gray none; each dot has a 2px ring of card background
- **progress_fill:** 0 0 8px <fill colour> @ .35-.55
- **chat_avatar_ring:** 0 0 10px rgba(58,115,200,.45)
- **nav_active_icon:** 0 0 10px rgba(99,170,251,.45)
- **orb:** see orb.glow

## 6. Radii and spacing

Measured radii (diagonal border-peak / fill-threshold fits):

| Element | r | Element | r |
|---|---|---|---|
| hero | 15 | right_panel | 15 |
| quickstats | 13 | activity | 13 |
| confirm_card | 14 | composer | 13 |
| header_pill | 14 | nav_active | 11 |
| hero_buttons | 13 | deny_approve | 12.5 |
| tiles | 13 | code_block | 12 |
| input | 11 | send_button | 12.5 |
| chat_bubbles | 13.5 | requires_pill | full (h30) |
| badge | circle d26 | progress | full (h9) |

Proposed scale: `--r-sm:12px; --r-md:14px; --r-lg:16px; --r-full:9999px`. r-lg: hero + right panel; r-md: cards, confirm card, composer, header pills, tiles, bubbles, hero buttons; r-sm: nav item, deny/approve, input, send, code.

Spacing (measured px):

- **page:** sidebar_to_content 23, content_right_margin 17, content_bottom_margin 28, column_gap 18, row_gap 16, lower_cards_gap 15
- **hero:** orb_center [534, 319], orb_center_from_card_left 239, text_column_x 742, text_column_from_card_left 447, buttons_row_y [476, 531], button_gap [15, 15], quote_baseline_from_bottom 22
- **card_header:** padding_left_to_icon 22.5, icon_to_title 22.5, title_baseline_to_sub_baseline 22.5, chevron_right_padding 24, padding_top_to_title_cap 21.5
- **tiles:** padding_left 18, icon_to_value 27, gap_x 15, gap_y 14, card_padding_left 20, card_padding_right 15, bar_inset_x [16, 17], bar_from_tile_bottom [13, 15]
- **activity_items:** pitch 53.5, dot_center_x_from_card_left 30, text_x_from_card_left 63, time_right_padding 21, title_to_sub_baseline 21
- **right_panel:** tabs_y_band [125, 180], tab_icon_to_label 16.5, confirm_inset [19, 17], confirm_padding [23, 24], confirm_button_gap 15, avatar_to_bubble 13, bubble_padding [16, 20], bubble_gap 23, composer_inset [15, 16], composer_padding [15, 15]
- **nav:** item_height 61, item_pitch 64, item_x [16, 256], icon_x 42, label_x 93, icon_to_label 28
- **header:** pill_height 54, pill_gap [16, 15], pill_padding_x 20, pill_icon_to_text 16, divider_to_avatar 51, avatar_to_greeting 20

## 7. Typography

### 7.1 Font identification

Method: 17 reference text crops (alpha maps) were compared with Chromium renders of 28 sans families x 4-6 weights and 14 monospace families (Cascadia Mono is not installed here, so it was excluded). Each line was split into words, scaled to the reference line height, and scored by word NCC minus width error. The requested list (Inter, Poppins, Plus Jakarta Sans, Manrope, Outfit, Sora, DM Sans, Urbanist, Lexend) was tested along with 18 others plus local Segoe UI and Consolas.

| Rank | Body text (10 samples) | score | Headings (7 samples) | score | Mono | score |
|---|---|---|---|---|---|---|
| 1 | Segoe UI | 0.7256 | Figtree | 0.7184 | Source Code Pro | 0.567 |
| 2 | Source Sans 3 | 0.661 | Onest | 0.6948 | JetBrains Mono | 0.563 |
| 3 | Roboto | 0.6542 | Albert Sans | 0.6718 | DM Mono | 0.5246 |
| 4 | Noto Sans | 0.6358 | Inter | 0.6524 | Inconsolata | 0.5146 |
| 5 | Open Sans | 0.6118 | Poppins | 0.6481 | Red Hat Mono | 0.5005 |
| 6 | Urbanist | 0.6034 | Red Hat Display | 0.6454 | Fira Mono | 0.4999 |
| 7 | Mulish | 0.5572 | Outfit | 0.6321 | IBM Plex Mono | 0.4901 |
| 8 | Red Hat Display | 0.5259 | Lexend | 0.6182 | Roboto Mono | 0.4378 |
| 9 | Hanken Grotesk | 0.5169 | Rubik | 0.6071 | Cousine | 0.4341 |
| 10 | Outfit | 0.5068 | Urbanist | 0.5992 | Fira Code | 0.4256 |

Requested families (body / heading score): Inter 0.5055 / 0.6524; Poppins 0.4186 / 0.6481; Plus Jakarta Sans 0.415 / 0.5041; Manrope 0.4307 / 0.427; Outfit 0.5068 / 0.6321; Sora 0.2071 / 0.4097; DM Sans 0.457 / 0.5521; Urbanist 0.6034 / 0.5992; Lexend 0.4458 / 0.6182.

Brand words, per word NCC: hero "JARVIS" Montserrat 700 **.85** (width 1.01), Albert Sans 800 .84, Work Sans 700 .82, Poppins 800 .75, Inter 800 .62, Segoe UI 700 .33. Wordmark "JARVIS & ANNA" Montserrat 600 **.83**, Work Sans 600 .81, Onest 800 .77, Poppins 800 .74.

**Recommendation:**
```css
--font-ui:    "Segoe UI", "Noto Sans", "Open Sans", system-ui, -apple-system, sans-serif; /* owner runs Windows: exact match, works offline */
--font-brand: "Montserrat", "Segoe UI", sans-serif;   /* JARVIS hero 700, sidebar wordmark 600 */
--font-mono:  "JetBrains Mono", "Source Code Pro", Consolas, monospace;
```
If only Google Fonts may be used: body Noto Sans or Source Sans 3, headings Figtree 700-800, brand Montserrat. Noto Sans and Open Sans share Segoe UI's designer (Steve Matteson) and its humanist shapes.

### 7.2 Type scale (sizes from ink width and cap height, Segoe UI cap height = .70em)

| Role | Size px | Weight | Letter-spacing | Line-height | Family |
|---|---|---|---|---|---|
| hero-title | 64 | 700 | -0.5 | - | brand |
| page-title | 30 | 700 | 0 | - | ui |
| hero-role | 26 | 400 | 0 | - | ui |
| brand-wordmark | 21 | 600 | -0.3 | - | brand |
| hero-status | 21 | 600 | 0 | - | ui |
| card-title / tab / nav | 17 | 700 (titles, active tab) / 600 (active nav) / 400 (inactive) | 0 | - | ui |
| value | 17 | 700 | 0 | - | ui |
| body / chat / input / buttons / header subtitle / greeting | 16 | 400 (body) / 600 (buttons) | 0 | 24 | ui |
| pill / activity title | 15 | 400 / 600 | 0 | - | ui |
| code | 15 | 400 | 0 | 24 | mono |
| card subtitle / item sub / sidebar subtitle / time | 14 | 400 | 0 | - | ui |
| eyebrow | 14 | 400 | 4.3 | - | ui |
| quote | 14 | 400 | 1.6 | - | ui |
| assistant label | 14 | 700 | 0.3 | - | ui |
| hint / chat time / pill badge | 13 | 400 | 0 | - | ui |
| tile label | 12.5 | 400 | 0 | - | ui |
| sidebar footer | 11 | 600 | 4.0 | 22 | ui |

### 7.3 Every text element

Cap-top and baseline are absolute y values. Ink box is the tight glyph box (descenders included).

| Element | Text | Ink box | Cap top / base | Font | Size / wt | LS | Colour |
|---|---|---|---|---|---|---|---|
| sidebar.brand_title | JARVIS & ANNA | 54,103 165x15 | 103 / 117 | Brand | 21 / 600 | -0.3 | `#ffffff` |
| sidebar.brand_subtitle | Your AI Command Center | 53,131 168x10 | 131 / 140 | UI | 14 / 400 | 0.3 | `#7e89ac` |
| sidebar.nav.overview | Overview | 93,210 74x13 | 210 / 222 | UI | 17 / 600 | 0 | `#ffffff` |
| sidebar.nav.agents | Agents | 93,275 55x17 | 275 / 286 | UI | 17 / 400 | 0 | `#a5b0dc` |
| sidebar.nav.activity | Activity | 93,338 59x18 | 338 / 350 | UI | 17 / 400 | 0 | `#a5b2dc` |
| sidebar.nav.settings | Settings | 93,401 64x22 | 401 / 417 | UI | 17 / 400 | 0 | `#a2aed6` |
| sidebar.footer.line1 | A SMARTER | 36,858 94x8 | 858 / 865 | UI | 11 / 600 | 4.0 | `#5c6a94` |
| sidebar.footer.line2 | TOMORROW TOGETHER | 36,880 192x9 | 880 / 887 | UI | 11 / 600 | 4.0 | `#5e6a95` |
| header.title | Overview | 315,29 140x23 | 29 / 51 | UI | 30 / 700 | 0 | `#ffffff` |
| header.subtitle | Control, collaborate, and automate with JARVIS & ANNA. | 314,67 416x14 | 68 / 78 | UI | 16 / 400 | 0 | `#abb6de` |
| header.pill.system | System Online | 813,50 95x11 | 50 / 60 | UI | 15 / 400 | 0 | `#f2f5fa` |
| header.pill.devices | 3 Connected Devices | 997,50 131x11 | 50 / 60 | UI | 15 / 400 | 0 | `#f2f5fa` |
| header.pill.voice | Voice Ready | 1216,50 80x15 | 50 / 60 | UI | 15 / 400 | 0 | `#f2f5fa` |
| header.avatar | AM | 1431,49 30x14 | 49 / 62 | UI | 19 / 700 | 0 | `#74b9fd` |
| header.greeting.line1 | Good evening, | 1495,36 100x16 | 36 / 47 | UI | 16 / 400 | 0 | `#a7b0d2` |
| header.greeting.name | Alex | 1495,62 34x13 | 62 / 74 | UI | 17 / 700 | 0 | `#ffffff` |
| hero.eyebrow | JARVIS & ANNA | 742,188 154x10 | 188 / 197 | UI | 14 / 400 | 4.3 | `#92a3d4` |
| hero.title | JARVIS | 742,225 241x46 | 225 / 270 | Brand | 64 / 700 | -0.5 | `#e4eafc` |
| hero.role | AI Assistant | 743,290 134x18 | 290 / 307 | UI | 26 / 400 | 0 | `#8192cc` |
| hero.status | Listening for your command | 743,404 258x19 | 404 / 418 | UI | 21 / 600 | 0 | `#8dc4ff` |
| hero.btn.start | Start Listening | 479,498 103x12 | 498 / 509 | UI | 16 / 600 | 0 | `#ffffff` |
| hero.btn.run | Run Task | 714,498 63x12 | 498 / 509 | UI | 16 / 600 | 0 | `#ffffff` |
| hero.btn.micoff | Mic Off | 924,498 54x12 | 498 / 509 | UI | 16 / 600 | 0 | `#fcfdff` |
| hero.quote | “Ideas become reality, faster.” | 578,558 232x10 | 558 / 567 | UI | 14 / 400 | 1.6 | `#95aadf` |
| quickstats.title | Quick Stats | 362,627 92x12 | 627 / 638 | UI | 17 / 700 | 0 | `#ffffff` |
| quickstats.subtitle | Live system status | 362,651 116x11 | 651 / 661 | UI | 14 / 400 | 0 | `#a0acca` |
| quickstats.tile.cpu.value | 21% | 387,706 32x13 | 706 / 718 | UI | 17 / 700 | 0 | `#ffffff` |
| quickstats.tile.cpu.label | CPU Usage | 387,728 61x9 | 728 / 736 | UI | 12.5 / 400 | 0 | `#97a2c0` |
| quickstats.tile.memory.value | 63% | 582,706 35x13 | 706 / 718 | UI | 17 / 700 | 0 | `#ffffff` |
| quickstats.tile.memory.label | Memory | 582,728 45x9 | 728 / 736 | UI | 12.5 / 400 | 0 | `#97a2c0` |
| quickstats.tile.disk.value | 82% | 387,812 36x12 | 812 / 823 | UI | 17 / 700 | 0 | `#ffffff` |
| quickstats.tile.disk.label | Disk (C:) | 387,834 50x10 | 834 / 843 | UI | 12.5 / 400 | 0 | `#97a2c0` |
| quickstats.tile.devices.value | 3 | 582,812 8x12 | 812 / 823 | UI | 17 / 700 | 0 | `#ffffff` |
| quickstats.tile.devices.label | Connected Devices | 581,834 99x10 | 834 / 843 | UI | 12.5 / 400 | 0 | `#97a2c0` |
| activity.title | Recent Activity | 787,629 123x16 | 629 / 640 | UI | 17 / 700 | 0 | `#ffffff` |
| activity.subtitle | Latest events from JARVIS & ANNA | 787,653 222x10 | 653 / 662 | UI | 14 / 400 | 0 | `#9baacb` |
| activity.item1.title | System online | 784,691 94x14 | 691 / 701 | UI | 15 / 600 | 0 | `#ffffff` |
| activity.item1.subtitle | All systems operational | 784,713 147x10 | 713 / 722 | UI | 14 / 400 | 0 | `#94a1bf` |
| activity.item1.time | 9:41 PM | 1055,694 49x10 | 694 / 703 | UI | 14 / 400 | 0 | `#7e8db4` |
| activity.item2.title | Voice service ready | 784,744 128x15 | 744 / 754 | UI | 15 / 600 | 0 | `#ffffff` |
| activity.item2.subtitle | Microphone initialized | 785,766 136x10 | 766 / 775 | UI | 14 / 400 | 0 | `#94a1bf` |
| activity.item2.time | 9:40 PM | 1055,747 49x11 | 747 / 757 | UI | 14 / 400 | 0 | `#7e8db4` |
| activity.item3.title | Laptop connected | 785,798 121x14 | 798 / 808 | UI | 15 / 600 | 0 | `#ffffff` |
| activity.item3.subtitle | Windows 11 • 3 devices online | 784,819 192x11 | 819 / 829 | UI | 14 / 400 | 0 | `#94a1bf` |
| activity.item3.time | 9:38 PM | 1055,800 49x11 | 800 / 810 | UI | 14 / 400 | 0 | `#7e8db4` |
| activity.item4.title | Agent sync complete | 784,852 142x15 | 852 / 862 | UI | 15 / 600 | 0 | `#ffffff` |
| activity.item4.subtitle | JARVIS & ANNA ready | 784,874 138x11 | 874 / 883 | UI | 14 / 400 | 0 | `#94a1bf` |
| activity.item4.time | 9:37 PM | 1055,855 49x11 | 855 / 865 | UI | 14 / 400 | 0 | `#7e8db4` |
| panel.tab.chat | Chat | 1209,142 35x12 | 142 / 153 | UI | 17 / 700 | 0 | `#ffffff` |
| panel.tab.approvals | Approvals | 1346,145 75x16 | 145 / 156 | UI | 17 / 400 | 0 | `#a7b1ce` |
| panel.tab.approvals.badge | 1 | 1442,146 4x11 | 146 / 156 | UI | 13 / 700 | 0 | `#f6fafd` |
| panel.tab.tools | Tools | 1573,141 38x13 | 141 / 153 | UI | 17 / 400 | 0 | `#abb6da` |
| panel.confirm.title | Confirm Command | 1226,231 146x12 | 231 / 242 | UI | 17 / 700 | 0 | `#ffffff` |
| panel.confirm.badge | Requires approval | 1506,233 98x12 | 233 / 242 | UI | 13 / 500 | 0 | `#e6a64e` |
| panel.confirm.question | Run this PowerShell command on your laptop? | 1186,274 336x16 | 274 / 285 | UI | 16 / 400 | 0 | `#b4bfd9` |
| panel.confirm.code.line1 | Get-Process \| Sort-Object CPU -Descending \| | 1205,331 380x12 | 331 / 342 | Mono | 15 / 400 | 0 | `#e6e7ec` |
| panel.confirm.code.line2 | Select-Object -First 5 | 1205,355 184x11 | 355 / 365 | Mono | 15 / 400 | 0 | `#e6e7ec` |
| panel.confirm.deny | Deny | 1270,419 37x11 | 419 / 429 | UI | 16 / 600 | 0 | `#ffffff` |
| panel.confirm.approve | Approve & Run | 1458,419 108x11 | 419 / 429 | UI | 16 / 600 | 0 | `#ffffff` |
| panel.chat.user.avatar | A | 1187,525 12x14 | 525 / 538 | UI | 19 / 600 | 0 | `#c6d3f4` |
| panel.chat.user.bubble.time | 9:42 PM | 1571,549 47x9 | 549 / 557 | UI | 13 / 400 | 0 | `#7186b4` |
| panel.chat.user.bubble.line1 | Show me the top 5 processes by CPU | 1250,519 266x12 | 519 / 530 | UI | 16 / 400 | 0 | `#ced8ee` |
| panel.chat.user.bubble.line2 | usage on my laptop. | 1250,543 144x16 | 543 / 554 | UI | 16 / 400 | 0 | `#ced8ee` |
| panel.chat.jarvis.bubble.label | JARVIS | 1249,622 48x10 | 622 / 631 | UI | 14 / 700 | 0.3 | `#acc8f9` |
| panel.chat.jarvis.bubble.time | 9:42 PM | 1571,622 48x10 | 622 / 631 | UI | 13 / 400 | 0 | `#5d739f` |
| panel.chat.jarvis.bubble.line1 | I’ll run that command for you after approval. | 1250,650 311x12 | 650 / 661 | UI | 16 / 400 | 0 | `#b0bcdb` |
| panel.chat.jarvis.bubble.line2 | This will show the top 5 processes by CPU | 1249,674 300x12 | 674 / 685 | UI | 16 / 400 | 0 | `#b0bcdb` |
| panel.chat.jarvis.bubble.line3 | usage, sorted from highest to lowest. | 1250,698 265x13 | 699 / 710 | UI | 16 / 400 | 0 | `#b0bcdb` |
| panel.composer.input | Message JARVIS... | 1191,804 122x12 | 804 / 815 | UI | 16 / 400 | 0 | `#94a4ca` |
| panel.composer.hint.attach | Shift + Enter for new line | 1211,867 142x10 | 867 / 876 | UI | 13 / 400 | 0 | `#96a3be` |
| panel.composer.hint.mic | Use microphone | 1524,867 94x10 | 867 / 876 | UI | 13 / 400 | 0 | `#8e9ab7` |

Line heights from consecutive baselines: chat and code lines 24px; header title baseline 51 to subtitle baseline 78; card title to subtitle 22-23px; activity title to sub 21px; activity item pitch 53.5px; sidebar footer lines 22px.

## 8. Components

### 8.1 Sidebar
| Element | Box | Radius | Fill | Border / extra |
|---|---|---|---|---|
| sidebar | 0,0 271x941 | - | #0a1124 (top-left #0f1631 fading by y=185; wave glow bottom-left) | right: 1px #1f2a48 at x=271 (measured #1d2845..#222c4b, AA at 270/272) |
| sidebar.logo | 107,26 59x58 | - | conic gradient ring, see logo spec | centre [136, 54.5] |
| sidebar.nav.overview | 16,185 241x61 | 11 | linear-gradient(90deg,#16285b 0%,#162753 10%,#16244b 25%,#172347 45%,#172141 70%,#172141 100%) | 1px rgba(120,145,215,0.16) (top #1a2952..#253256, right #243051, bottom #192a53..#202a48); accent 2px #3b82f6-ish (#4688d6/#3381e6) x=16..17, y=188..243, inner glow to x=21 |
| sidebar.nav.agents | 16,251 241x61 | - | - | transparent, no visible box (only icon + label) |
| sidebar.nav.activity | 16,315 241x61 | - | - | transparent, no visible box (only icon + label) |
| sidebar.nav.settings | 16,379 241x61 | - | - | transparent, no visible box (only icon + label) |
| sidebar.wave | 0,640 271x201 | - | - | silk-ribbon waves: 1px bright crest line + blurred ribbon fill, fading left->right; SVG paths + blur(6px) |

Nav: items are 61px tall at a 64px pitch (centres y 215, 281.5, 345.5, 409.5). Icon 24px at x 42-65, label at x 93. Only the active item has a box. Its 2px left accent bar is inset 3px top and bottom and bleeds a blue glow about 4px into the fill.

### 8.2 Header
| Element | Box | Radius | Fill | Border / extra |
|---|---|---|---|---|
| header.pill.system | 764,29 163x54 | 14 | #15233f | 1px #21405a near dot -> #203152 right (teal tint from dot glow); dot d12 at [790.5, 55.5] #22c086 (core #2acc91) |
| header.pill.devices | 943,29 205x54 | 14 | #132145 | 1px #243765 (top-left) -> #1c2749 (right) |
| header.pill.voice | 1163,29 153x54 | 14 | #111f43 | 1px #1a2d5d -> #1b2647 |
| header.divider | 1366,40 1x32 | - | - | colour #354369 (AA #1d2848 at x=1365) |
| header.avatar | 1417,27 59x59 | - | linear-gradient(160deg,#0f2a70 0%,#0e2460 35%,#0d1c47 70%,#0d1838 100%) | 1px #213977 (top) .. #314170 (bottom); centre [1446, 56] |
| header.chevron | 1618,53 13x7 | - | - |  |

### 8.3 Hero card
| Element | Box | Radius | Fill | Border / extra |
|---|---|---|---|---|
| hero | 295,116 830x474 | 15 | #0c1329 base (center #0b1126..#0d142a) | 1px gradient: top-left #2f3b81/#384694, top-mid #202b4b, top-right #25498d/#2552a4, bottom #172648..#1d2c55 (~rgba(110,140,255,.30) top corners -> .12 bottom) |
| hero.rule | 742,330 57x3 | - | - | colour #6290f2 (2px core at y=331, AA rows 330/332) |
| hero.waveform | 742,363 212x23 | - | - |  |
| hero.btn.start | 403,476 217x56 | 13 | linear-gradient(115deg,#3aa8fd 0%,#3189fe 22%,#2d66fc 48%,#2645f8 78%,#2541f8 88%,#2a5ffb 100%) | 1px inner highlight rgba(150,210,255,.35) top/left (#5db0e9 at top edge); shadow 0 6px 22px rgba(37,90,255,.45), 0 0 0 1px rgba(80,150,255,.25) |
| hero.btn.run | 635,476 193x56 | 13 | #121b34 (rgba(22,32,60,.85)) | 1px #232e4c |
| hero.btn.micoff | 843,476 180x56 | 13 | #18213b | 1px #2a3450 |

Inside the hero: orb centre (534,319) is 239px from the card's left edge and 203px from its top. The text column starts at x 742 (447px in). Eyebrow cap-top 188, JARVIS 225-270, role 290-307, rule at y 331, waveform centre y 374, status 404-418. The button row (y 476-531) has 15px gaps. The quote baseline sits 22px above the card bottom.

### 8.4 Quick Stats
| Element | Box | Radius | Fill | Border / extra |
|---|---|---|---|---|
| quickstats | 295,606 411x306 | 13 | linear-gradient(180deg,#0e152d 0%,#0d1529 25%,#0b1326 50%,#0a1325 100%) | 1px #131e3b (top) -> #0f1a32 (bottom), sides #15213a |
| quickstats.tile.cpu | 315,682 180x91 | 13 | #151e34 | 1px #1e2a43 (top) / #1c263e (bottom) |
| quickstats.tile.memory | 510,682 180x91 | 13 | #151e34 | 1px #1e2a43 (top) / #1c263e (bottom) |
| quickstats.tile.disk | 315,787 180x95 | 13 | #151e34 | 1px #1e2a43 (top) / #1c263e (bottom) |
| quickstats.tile.devices | 510,787 180x95 | 13 | #151e34 | 1px #1e2a43 (top) / #1c263e (bottom) |

| Tile | Icon (box) | Value / label | Track box | Fill box (measured ratio) | Fill colour | Fill glow |
|---|---|---|---|---|---|---|
| cpu | cpu 333,704 28x26 `#4ce0bf (stroke #2ecc8f)` | 21% / CPU Usage | 331,751 147x9 `#222b46 (rgba(148,163,184,.12))` | 331,751 38x9 (0.259) | `#35b992 (left cap #3ed29e)` | 0 0 8px rgba(52,185,146,.45) |
| memory | memory-stick 528,707 28x22 `#65b7fa` | 63% / Memory | 526,751 147x9 `#222b46 (rgba(148,163,184,.12))` | 526,751 93x9 (0.633) | `#3590fc -> #4898f9` | 0 0 8px rgba(56,146,252,.55) |
| disk | hard-drive 334,812 26x23 `#fbc551` | 82% / Disk (C:) | 331,858 147x9 `#222b46 (rgba(148,163,184,.12))` | 331,858 117x9 (0.796) | `#f9bc54 (right cap #fcc05e)` | 0 0 8px rgba(249,188,84,.35) |
| devices | laptop 529,813 27x21 `#bfbdf9` | 3 / Connected Devices | 526,858 147x9 `#222b46 (rgba(148,163,184,.12))` | 526,858 40x9 (0.272) | `#8981f6` | 0 0 8px rgba(137,129,246,.35) |

Tile inner layout: icon at +18px, value at +72px (27px after icon), label 22px below value baseline, track inset 16px left / 17px right and 13-15px above tile bottom, height 9, fully rounded.

### 8.5 Recent Activity
| Element | Box | Radius | Fill | Border / extra |
|---|---|---|---|---|
| activity | 721,607 404x305 | 13 | linear-gradient(180deg,#0e152b 0%,#0d1528 50%,#0b1326 100%) | 1px #121e36 (top) -> #101b31 (bottom) |
| activity.timeline.line | 751,708 1x148 | - | - | colour #25324c..#2d3654 (1px core, AA neighbours #151f36) - slight tint of adjacent dot colour near each dot |

| Item | Dot (centre, colour, glow) | Title box | Sub box | Time box |
|---|---|---|---|---|
| 1 | [751, 701] `#23b084` none/very faint | System online 784,691 94x14 | All systems operational 784,713 147x10 | 9:41 PM 1055,694 49x10 |
| 2 | [751, 754] `#4a95fb` 0 0 8px rgba(74,149,251,.55) | Voice service ready 784,744 128x15 | Microphone initialized 785,766 136x10 | 9:40 PM 1055,747 49x11 |
| 3 | [751, 807] `#7a50f9` 0 0 8px rgba(122,80,249,.5) | Laptop connected 785,798 121x14 | Windows 11 • 3 devices online 784,819 192x11 | 9:38 PM 1055,800 49x11 |
| 4 | [751, 861.5] `#8895bc` none | Agent sync complete 784,852 142x15 | JARVIS & ANNA ready 784,874 138x11 | 9:37 PM 1055,855 49x11 |

Dots are 13px with a 2px ring of card colour that cuts the 1px connector line (x 751, `#2b3552`).

### 8.6 Right panel
| Element | Box | Radius | Fill | Border / extra |
|---|---|---|---|---|
| panel | 1143,114 512x800 | 15 | linear-gradient(180deg,#10172f 0%,#0d1427 11%,#0b1224 17%,#0a1123 40%,#0a1123 100%) | 1px #232d47 (top) -> #18223a (sides) -> #111a30 (bottom) |
| panel.tab.chat | 1151,125 123x56 | - | - | underline 1151,178 123x3 #4ba8ff (AA #408dde/#26549d), glow 0 0 10px 1px rgba(75,168,255,.45) (glow visible y 170..186) |
| panel.tab.approvals | 1290,125 181x56 | - | - |  |
| panel.tab.tools | 1525,125 106x56 | - | - |  |
| panel.tabs.divider | 1144,179 510x2 | - | - | colour #161d32/#172035 (~rgba(148,163,184,.08)) |
| panel.confirm | 1162,202 476x268 | 14 | #151826 (warm-tinted surface; ~rgba(255,255,255,.035) + amber tint) | 1px #362a2a..#3f302a (amber ~rgba(245,158,11,.18)) |
| panel.confirm.badge | 1471,223 146x30 | 15 | #2e231e | 1px #4f392a/#523a26 (amber ~rgba(240,166,76,.30)) |
| panel.confirm.code | 1184,308 433x80 | 12 | #060c1c | 1px #151d2f |
| panel.confirm.deny | 1184,400 209x49 | 12 | #131b30 | 1px #2e364f |
| panel.confirm.approve | 1408,400 209x49 | 12 | #2760fb + inner glow: radial(120% 140% at 50% 50%, #245afb 45%, #2f78fd 100%) (edges #2f75fc, centre #265cfb) | shadow none visible outside |
| panel.chat.user.avatar | 1171,509 45x46 | - | #36405c | centre [1193, 531.5] |
| panel.chat.user.bubble | 1229,499 409x79 | 14 | #192139 | none (top edge 1px #1c253d barely visible) |
| panel.chat.jarvis.avatar | 1171,610 45x46 | - | - | outer ring d44 1.5px #3a73c8 (#448ed9 peak), glow 0 0 10px rgba(58,115,200,.45); centre [1193, 632.5] |
| panel.chat.jarvis.bubble | 1229,601 409x130 | 14 | #121b32 (left edge #131b32 -> #141d34 very slight) |  |
| panel.composer | 1158,770 481x129 | 14 | linear-gradient(180deg,#121b32 0%,#0f192f 30%,#0e172c 100%) | 1px #23304e (top) -> #131f36 (bottom), sides #1d2742 |
| panel.composer.input | 1173,785 450x58 | 12 | #15203d | 1px #1b2744 |
| panel.composer.send | 1570,787 54x54 | 12 | #163b80 (#143a7f..#173e83) | none/1px #1a376c |

Approvals badge: 26px circle centred (1444,151), fill `#3a5fd2`, white 13px/700 "1". JARVIS chat avatar: 44px outer ring (1.5px `#3a73c8` + 10px glow) around a 34px logo ring with a 20px hole on `#060d1f`. Code block text: 15px mono, 24px line height, first glyph at x 1205 (21px padding), lines centred in an 80px box.

## 9. Icons

Style: **Lucide-style outline icons, stroke 2px at 24px, round caps and joins** (measured 1.8-2.6px). Primary and active icons use the **filled** variant (house, mic, zap, send, sparkle). Tab icons are 20px (about 1.7px stroke) and hint icons 16-18px (about 1.45px).

| Where | Lucide name | Box | Size | Stroke | Colour | Filled |
|---|---|---|---|---|---|---|
| sidebar.nav.overview | house (filled) | 42,204 24x24 | 24 | - | `#63aafb` | yes |
| sidebar.nav.agents | users | 41,272 25x20 | 24 | 2.0 | `#acb9e2` |  |
| sidebar.nav.activity | activity | 42,336 24x19 | 24 | 2.0 | `#b0bbe3` |  |
| sidebar.nav.settings | settings | 42,398 24x23 | 24 | 2.0 | `#b7c3e7` |  |
| header.pill.devices | monitor | 960,47 23x18 | 24 | 2.0 | `#66b8fc` |  |
| header.pill.voice | mic (filled capsule) | 1183,44 18x24 | 24 | - | `#69b3fc` | yes |
| header.chevron | chevron-down | 1618,53 13x7 | 16 | 2.0 | `#adbae8` |  |
| hero.btn.start | mic (filled) | 445,492 18x24 | 24 | - | `#ffffff` | yes |
| hero.btn.run | zap (filled) | 680,493 15x22 | 24 | - | `#cdd9fb` | yes |
| hero.btn.micoff | mic-off | 889,492 20x24 | 24 | 1.8 | `#cedbf6` |  |
| quickstats.icon | chart-no-axes-combined (bars + trend arrow) | 318,631 22x23 | 24 | 2.0 | `#80befd` |  |
| quickstats.chevron | chevron-right | 675,638 7x11 | 16 | 2.0 | `#abb8e2` |  |
| quickstats.tile.cpu | cpu | 333,704 28x26 | 26 | 2.0 | `#4ce0bf (stroke #2ecc8f)` |  |
| quickstats.tile.memory | memory-stick | 528,707 28x22 | 26 | 2.0 | `#65b7fa` |  |
| quickstats.tile.disk | hard-drive | 334,812 26x23 | 26 | 2.0 | `#fbc551` |  |
| quickstats.tile.devices | laptop | 529,813 27x21 | 26 | 2.0 | `#bfbdf9` |  |
| activity.icon | clock | 743,632 23x24 | 24 | 2.25 | `#85bcfd` |  |
| activity.chevron | chevron-right | 1094,639 7x11 | 16 | 2.0 | `#a4b1dc` |  |
| panel.tab.chat | message-circle | 1173,138 20x20 | 20 | 1.9 | `#55b0fb` |  |
| panel.tab.approvals | clipboard-check | 1312,139 18x19 | 20 | 1.7 | `#afb9db` |  |
| panel.tab.tools | box | 1539,138 19x21 | 20 | 1.7 | `#adb8db` |  |
| panel.confirm.icon | shield-alert | 1186,225 21x25 | 26 | 2.0 | `#f9c74c` |  |
| panel.confirm.badge | sparkle (filled 4-point star) | 1483,233 11x10 | 12 | - | `#f1a74d` | yes |
| panel.composer.send | send (filled paper plane) | 1586,803 22x22 | 22 | - | `#eef3fd` | yes |
| panel.composer.hint.attach | paperclip | 1178,863 16x18 | 18 | 1.45 | `#b4bfdc` |  |
| panel.composer.hint.mic | mic (filled capsule) | 1493,863 15x19 | 18 | - | `#b4bfe2` | yes |

## 10. The orb

Centre **(534.1, 319.2)**, sphere radius **107px**. Least-squares fit of rim maxima over 120 angles, residual std 1.26px. Card-relative centre [239, 203]. Polar unwrap: `crops/32_orb_polar_unwrap.png`.

| Layer (inside to out) | Geometry | Colours / recipe |
|---|---|---|
| core hotspot | r [0, 12] | #ffffff, #8de5fe, #47acfc ; radial-gradient(circle, #fff 0, #8de5fe 4px, #47acfc 10px, rgba(47,130,244,0) 18px) |
| light streak / lens flare | angle_deg 22, length_px 180 | #ffffff core, #e9ffff, #8de5fe glow ; thin white-cyan line from ~(460,290) to ~(610,355) with soft glow, brightest at centre; the polar unwrap shows it as bright columns at 20deg and 200deg reaching r~90 |
| sphere body | r [12, 104] | dark interior #0e2262 at r 55-65, brightening to #3151cd at r95 and #5d8bf7 at r105 ; radial-gradient(circle, transparent 0 14px, #0e2262 55px, #102269 70px, #223fb0 90px, #5d8bf7 104px) |
| inner swirl ribbons | r [60, 100] | #2f6bff, #5a4bff, #9b6bff ; 3-5 translucent blue/violet arcs (orbit-like, intersecting) at opacity .25-.5; violet/magenta tint lower-right |
| rim | r 107, width 3 | see sphere.rim_colors_by_angle (conic)  |
| dark gap | r [109, 121] | #16216f  |
| orbit rings | radii [113.5, 124.5, 128, 134], stroke_px 1 | #2634a6, #3b5bd9 highlights, violet #6a5cff upper-right ; outermost ring centre offset (-3,-3.5) -> slight 3D tilt; bright specks at ~65deg and ~250deg |
| particle halo | r [140, 230], count_detected 92, size_px [1, 4], dotted_rings_r [147, 160, 175] | #1a2f81, #244fc9, #3d63d2, #5748f4, #9fd8ff (rare bright)  |
| outer glow | r [107, 185] |  ; radial-gradient(circle at center, rgba(40,90,255,.45) 107px, rgba(40,90,255,.18) 140px, rgba(40,90,255,0) 185px) + violet rgba(120,80,255,.25) bias on the right |

Rim colour by angle (clockwise from 3 o'clock): 0 deg `#becffc`, 18 deg `#f1e4ff`, 36 deg `#e8d6fe`, 54 deg `#f5f5ff`, 72 deg `#e9ffff`, 90 deg `#acd9ff`, 108 deg `#8abdff`, 126 deg `#a0e5ff`, 144 deg `#9bd5fb`, 162 deg `#a2e5fb`, 180 deg `#c7f3fe`, 198 deg `#cdf5fe`, 216 deg `#d5ffff`, 234 deg `#c4f9fd`, 252 deg `#ecfbff`, 270 deg `#b0e7ff`, 288 deg `#9dc8fd`, 306 deg `#8796ff`, 324 deg `#809df0`, 342 deg `#78b4fa`. brightest cyan-white arc on the left/upper-left (180-260deg), white-pink highlight lower-right (20-60deg), dimmer blue-violet upper-right (290-340deg).

Radial mean profile: r0 `#f6fefd`, r5 `#8de5fe`, r10 `#47acfc`, r15 `#2f82f4`, r20 `#2368e1`, r30 `#1747b2`, r40 `#123288`, r50 `#0e256a`, r60 `#0e2164`, r70 `#102269`, r80 `#152b86`, r90 `#223fb0`, r100 `#4667e4`, r105 `#5d8bf7`, r110 `#232fa6`, r120 `#16216f`, r125 `#2634a6`, r130 `#25339e`, r135 `#25328a`, r140 `#152166`, r150 `#101b4a`, r160 `#111e4a`, r180 `#132250`, r200 `#142252`, r220 `#13204a`.

Build recipe (canvas or layered SVG, 460x460 box centred on the orb):
1. Outer glow: radial-gradient rgba(40,90,255,.45) at r107 to .18 at r140 to 0 at r185. Bias violet rgba(120,80,255,.25) toward the right/upper-right.
2. Particle halo: about 90 dots of 1-4px in r 140-230, denser on dotted rings r 147/160/175, colours `#1a2f81`..`#3d63d2`, a few violet `#5748f4`, rare bright `#9fd8ff`; slow drift. All detected positions are in `forensics.json > orb.layers_inside_out[7].particles`.
3. Orbit rings: 1px circles r 113.5, 124.5, 128, 134 (`#2634a6`, highlights `#3b5bd9`, violet upper-right). Offset the outermost by (-3,-3.5) to suggest tilt.
4. Sphere body: radial `#0e2262` (r55-65) to `#223fb0` (r90) to `#5d8bf7` (r104).
5. Swirl: 3-5 translucent arcs (opacity .25-.5) inside r 60-100, rotating slowly.
6. Rim: 3px conic stroke at r107 (cyan-white left, white-pink lower-right, blue-violet upper-right) with a 6px outer glow `#8fd8ff`.
7. Core: white hotspot r12 (`#fff` to `#8de5fe` to `#47acfc`) plus a 180px streak at 22 degrees.
8. States: idle: slow rotate swirl (20s) + particle drift; listening: rim brightens, waveform active; speaking: scale 1.0->1.03 pulse with audio level.

## 11. Waveform, logo ring, sidebar wave

**Waveform:** box 742,363 212x23, 31 rounded bars, pitch 7.1px, width about 3.5px, centre y 374. Tall (21-23px) bars at x [814, 834, 855, 878]. Dots 2-5px at both ends; 10-12px mid bars alternate with 21-23px peaks. Colour runs violet-blue (left) to cyan (centre) to blue (right) and fades out at both ends.

| x centre | h | colour |   | x centre | h | colour |
|---|---|---|---|---|---|---|
| 743.0 | 3 | `#3948a4` |   | 855.0 | 23 | `#53ccf9` |
| 750.5 | 4 | `#2b3887` |   | 864.0 | 12 | `#4cbdf3` |
| 757.0 | 3 | `#3443ae` |   | 870.5 | 10 | `#43aaee` |
| 763.5 | 4 | `#2e399b` |   | 878.0 | 23 | `#43b9f9` |
| 770.0 | 5 | `#3543b4` |   | 885.5 | 11 | `#3d9ef3` |
| 777.5 | 5 | `#4b56e2` |   | 892.5 | 8 | `#4998f3` |
| 784.5 | 6 | `#536ceb` |   | 899.5 | 12 | `#4682eb` |
| 792.0 | 11 | `#6880f8` |   | 907.5 | 6 | `#3e60d2` |
| 799.5 | 7 | `#577cf2` |   | 915.5 | 5 | `#2f49b0` |
| 807.0 | 10 | `#5c79f1` |   | 923.0 | 4 | `#293e94` |
| 814.0 | 21 | `#5a7bf7` |   | 930.5 | 4 | `#223781` |
| 820.5 | 10 | `#3b60d9` |   | 937.5 | 3 | `#1b2f69` |
| 827.5 | 12 | `#436ddd` |   | 944.0 | 2 | `#132049` |
| 834.0 | 23 | `#4799f9` |   | 946.5 | 3 | `#132252` |
| 841.0 | 11 | `#51b4f6` |   | 952.5 | 2 | `#122249` |
| 848.5 | 11 | `#5fc5f5` |   | | | |

**Logo ring (sidebar):** centre [136, 54.5], outer r 29, hole r 15.5. outer band r24-29 (darker), glossy inner tube r16-23; light-blue specular arc at top (#85b8fb), pink-white highlight lower-right (#ebbdff). CSS angle samples at r22.5: 0 deg `#2940a1`, 30 deg `#5b77e9`, 60 deg `#858cf9`, 90 deg `#9a82fc`, 120 deg `#d09ffd`, 150 deg `#ebbdff`, 180 deg `#bb99fc`, 210 deg `#6156e9`, 240 deg `#3339b7`, 270 deg `#1e288e`, 300 deg `#152570`, 330 deg `#1f3382`.
CSS: `background:conic-gradient(from 0deg, #4775f1 0deg, #80b1fd 30deg, #7d90fc 60deg, #876bfc 90deg, #d089f9 120deg, #ebbdff 150deg, #906afb 180deg, #554bf0 210deg, #4c4ced 240deg, #303fdc 270deg, #3056e5 300deg, #3f72ef 330deg, #4775f1 360deg); mask: radial-gradient(circle, transparent 15.5px, #000 16px 29px, transparent 29.5px)`. Add an inner highlight arc at the top (`#85b8fb`) and a lower-right specular (`#ebbdff`). No outer glow.

**Sidebar wave:** main crest polyline [[0, 687], [30, 690], [60, 698], [90, 709], [120, 725], [150, 744], [180, 768], [210, 790], [240, 811], [270, 830]]; lower crest [[0, 805], [30, 792], [60, 784], [90, 778], [120, 785], [150, 788], [180, 800], [210, 816], [240, 827]]; upper ribbon edge [[120, 676], [150, 680], [180, 688], [210, 694], [240, 698]]. Crest `#152c69` (left) fading to `#0d1431` (right); ribbon fill about `#101d49`. Draw as SVG cubic paths: a 1px bright stroke over a 6px-blurred fill, with a slight violet tint at the lower-left.

## 12. Normalise these AI-render irregularities

- Approvals tab label sits 3px lower than Chat/Tools (baseline 156 vs 153)
- Quick Stats inner padding is 20px left / 15px right; tiles 180px wide - use symmetric 18-20px
- Quote centre is x~694 while card centre is 709.5
- Right panel top (114) is 2px above hero top (116)
- Lower cards: 411px vs 404px wide; use equal columns
- Progress fills: CPU 25% drawn for 21%, Disk 79% drawn for 82%, Devices 27% for '3' - bind to real values
- 1px dark halos around bright text/borders are render sharpening - do not reproduce
- Text letter-spacing varies by ±0.5px between equal styles. Use 0 except for the explicit tracked styles: eyebrow, quote, footer, JARVIS label.

## 13. Crops index (`crops/`)

- `00_reference_full.png`: untouched reference
- `01_annotated_boxes_1.5x.png`: all measured boxes drawn over the reference (containers cyan, buttons yellow, text ink magenta, icons green, bars orange)
- `02_grid50_full.png`: 50px coordinate grid
- `03_glow_map_enhanced_x4.png / 04_luminance_map_x8.png`: contrast-stretched maps showing blooms, borders and glows
- `10-13`: sidebar top x4, nav x3, footer x2, wave enhanced
- `20-22`: header title x3, pills x2, avatar/greeting x3
- `30-38`: hero enhanced, orb x2, orb polar unwrap, text x2, buttons x2, top-right flare, bottom-left glow, waveform x6, corner x10
- `40-43`: quick stats x2, activity x2, timeline dots x5, progress bar x5
- `50-53`: tabs x2, confirm card x2, chat x2, composer x2
- `60-63`: icon sheet x8, logo ring x8, JARVIS chat avatar x8, header avatar x6
- `70-76`: font evidence: reference text crops and side-by-side renders (body, h1, hero JARVIS, brand, card title, mono)

## 14. Ready-to-paste CSS tokens

```css
:root {
  /* canvas and surfaces (measured) */
  --bg: #070e1f;               --bg-bloom: #0c183b;
  --sidebar: #0a1124;          --sidebar-top: #0f1631;      --sidebar-divider: #1f2a48;
  --surface-hero: #0c1329;
  --surface-card-top: #0e152d; --surface-card-bottom: #0a1325;
  --surface-panel-top: #10172f; --surface-panel: #0a1123;
  --surface-tile: #151e34;
  --surface-bubble-user: #192139; --surface-bubble-ai: #121b32;
  --surface-input: #15203d;    --surface-composer: #0e182c;  --surface-code: #060c1c;
  --surface-ghost: #121b34;    --surface-pill: #132145;
  --surface-warn: #151826;     --surface-warn-pill: #2e231e;
  --surface-avatar-user: #36405c;
  /* borders and dividers (1px, lit from the top) */
  --border-card: #121e37;      --border-panel-top: #232d47;  --border-panel-side: #18223a;
  --border-tile: #1e2a43;      --border-control: #2e364f;    --border-input: #1b2744;
  --border-code: #151d2f;      --border-warn: #3a2c29;       --border-warn-pill: #523a26;
  --divider: #172035;          --divider-header: #354369;
  /* text */
  --text-strong: #ffffff;      --text: #f2f5fa;
  --text-chat-user: #ced8ee;   --text-chat-ai: #b0bcdb;
  --text-secondary: #a7b1d3;   --text-muted: #97a3c2;        --text-dim: #7e8db4;   --text-faint: #5c6a94;
  --text-hero-title: #e4eafc;  --text-hero-role: #8192cc;    --text-eyebrow: #92a3d4; --text-quote: #95aadf;
  /* accents */
  --accent: #3b82f6;
  --accent-grad: linear-gradient(115deg,#3aa8fd 0%,#3189fe 22%,#2d66fc 48%,#2645f8 78%,#2a5ffb 100%);
  --accent-approve: radial-gradient(120% 140% at 50% 50%, #245afb 45%, #2f78fd 100%);
  --accent-light: #8dc4ff;     --accent-icon: #63aafb;       --accent-underline: #4ba8ff;
  --accent-rule: #6290f2;      --accent-ai-label: #acc8f9;   --badge: #3a5fd2;      --send: #163b80;
  /* status */
  --green: #22c086;  --blue: #4a95fb;  --violet: #7a50f9;  --gray: #8895bc;
  --amber: #f9bc54;  --amber-icon: #f9c74c;  --amber-text: #e6a64e;
  --bar-cpu: #35b992; --bar-mem: #3590fc; --bar-disk: #f9bc54; --bar-dev: #8981f6; --track: #222b46;
  /* glows */
  --glow-primary: 0 6px 22px rgba(37,90,255,.45);
  --glow-underline: 0 0 10px 1px rgba(75,168,255,.45);
  --glow-dot-green: 0 0 8px rgba(34,197,134,.45);
  /* radii */
  --r-sm: 12px; --r-md: 14px; --r-lg: 16px; --r-full: 9999px;
  /* type */
  --font-ui: "Segoe UI", "Noto Sans", "Open Sans", system-ui, -apple-system, sans-serif;
  --font-brand: "Montserrat", "Segoe UI", sans-serif;
  --font-mono: "JetBrains Mono", "Source Code Pro", Consolas, monospace;
}
```

## 15. How it was measured

- **Container edges:** luminance peaks of 1px borders across 9-15 scanlines per edge (median). Filled shapes use the max-gradient transition.
- **Radii:** the border or fill crossing along each corner's 45-degree diagonal, converted with r = t/(1-1/sqrt2).
- **Text and icon boxes:** grey-opening background subtraction, then thresholding and connected components. Glyph components give cap-top, baseline and x-height.
- **Font sizes:** from ink width (canvas `measureText` actualBoundingBox) and cap height, with Segoe UI and Montserrat cap height = .70em.
- **Colours:** 5x5 medians for surfaces; brightest 4% of glyph pixels for text.
- **Icon strokes:** 4x upsampled alpha, OpenCV thinning, alpha area divided by skeleton length.
- **Orb:** circle fit to rim maxima, polar unwrap, ring-radius histogram of local maxima, blob detection for particles.
- Scripts are in `work/` (common.py, scan*.py, comps*.py, glyphs*.py, fontrender.py, fontscore2.py, fontsize.py, orb*.py, wavebars2.py, icons2.py, build*.py).
