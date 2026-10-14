# Product website design

The public site explains Better Agent Dashboard; `desktop/DESIGN.md` remains the
source of truth for the app. This site uses an editorial product layout, with real
UI screenshots rather than a simulated console or decorative metrics.

- Retain the original site's ink `#0f1828` and pale blue `#bcd6ff`; use the app's
  paper `#f6f7f9` and a white page throughout, including installation.
  Use the app's action `#2f5bd3`, muted `#5d6676`, border `#dfe3ea` and white panel.
  All colors, sizes, spacing and radii are tokens in `styles.css`.
- System UI / Segoe UI / Noto Sans TC. Hero 32–56px; section 24–32px; body 17px;
  supporting copy 15px, labels 13px. Use a 4/8px spacing rhythm and 1180px max width.
- Flat sections, thin rules and calm hierarchy. No gradients, decorative shadows,
  fabricated statistics, fake testimonials or mock product functionality.
- One main action per section. Secondary links remain visibly secondary. Controls
  have 44px hit targets; keyboard focus is explicit. Mobile navigation collapses.
- Real screenshot gallery, semantic workflow list and an HTML architecture diagram.
  Lead with live Overview → record → PM discussion → reviewed adoption in one project.
  The eight-scene tour uses one synthetic project in both languages.
  Label synthetic demonstration data and distinguish target installation from current
  implementation. The development-preview scope is visible below the gallery and
  before installation. Keep capture hashes and reproduction detail in the linked
  source record, not the product introduction.
- English and Traditional Chinese share structure. URL language overrides saved
  preference; language switching preserves the section hash. Content remains readable
  without JavaScript; both screenshots stay visible in that case.
- Verify 390 / 768 / 1440px, both languages, keyboard interaction, image captions,
  overflow, broken local links and browser console errors before publishing.
- Use one light theme across the screenshot tour. The app still follows the user's
  system theme; mixing dark and light captures across tabs is not a theme control.
  Start the tour immediately after the compact introduction. Underlined blue tab
  selection and neutral controls keep the real product image as the focal point.
