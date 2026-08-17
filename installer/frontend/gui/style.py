# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Forge GUI — InterGenOS visual-language CSS layer.

2026-05-25 "shock and awe" pass: original palette/border/typography layer
preserved, Forge-specific classes added for the rebuilt _base + welcome
chrome (`forge-header-wordmark`, `forge-step-indicator`, `forge-welcome-*`).
Subsequent per-screen passes will lean on this same vocabulary so the
install flow reads as one continuous visual language.

Apply via `apply_intergenos_style()` once at application activate.
"""

from gi.repository import Gdk, Gtk


_INTERGENOS_CSS = """
window,
window.background,
.background {
  background-color: #050810;
  color: #e2e8f0;
  font-family: 'Inter', 'Cantarell', sans-serif;
}

headerbar,
.titlebar {
  background-color: #0a0e1a;
  color: #e2e8f0;
  border-bottom: 1px solid rgba(0, 153, 255, 0.08);
}

button {
  background-color: rgba(15, 21, 37, 0.5);
  color: #e2e8f0;
  border: 1px solid rgba(0, 153, 255, 0.08);
  border-radius: 8px;
  padding: 6px 16px;
  font-weight: 500;
}

button:hover {
  border-color: rgba(0, 153, 255, 0.22);
  background-color: rgba(0, 153, 255, 0.06);
  color: #ffffff;
}

button:active,
button:checked {
  border-color: rgba(0, 153, 255, 0.40);
  background-color: rgba(0, 153, 255, 0.12);
  color: #ffffff;
}

button:disabled {
  background-color: rgba(15, 21, 37, 0.25);
  color: #3d4f6a;
  border-color: rgba(0, 153, 255, 0.04);
}

button.suggested-action {
  background-color: rgba(0, 153, 255, 0.18);
  color: #ffffff;
  border-color: rgba(0, 153, 255, 0.40);
  font-weight: 600;
}

button.suggested-action:hover {
  background-color: rgba(0, 153, 255, 0.30);
  border-color: rgba(0, 153, 255, 0.65);
}

entry,
passwordentry,
.entry {
  background-color: #0f1525;
  color: #e2e8f0;
  border: 1px solid rgba(0, 153, 255, 0.08);
  border-radius: 10px;
  padding: 6px 10px;
  caret-color: #0099FF;
}

entry:focus,
passwordentry:focus {
  border-color: rgba(0, 153, 255, 0.40);
  background-color: #0a0e1a;
}

entry:disabled,
passwordentry:disabled {
  color: #3d4f6a;
}

label {
  color: #e2e8f0;
}

label.title-1 {
  font-weight: 700;
  font-size: 1.6em;
  color: #e2e8f0;
}

label.title-2 {
  font-weight: 600;
  font-size: 1.3em;
}

label.dim-label,
.dim-label {
  color: #7a8ba8;
  font-size: 0.85em;
}

label.warning,
.warning {
  color: #f59e0b;
}

label.error,
.error {
  color: #ef4444;
}

label.success,
.success {
  color: #10b981;
}

checkbutton check,
checkbutton radio {
  border: 1px solid rgba(0, 153, 255, 0.22);
  background-color: #0f1525;
  border-radius: 4px;
}

checkbutton:checked check,
checkbutton:checked radio {
  background-color: #0099FF;
  border-color: #0099FF;
}

progressbar {
  color: #e2e8f0;
}

progressbar trough {
  background-color: #0f1525;
  border: 1px solid rgba(0, 153, 255, 0.08);
  border-radius: 9999px;
  min-height: 12px;
}

progressbar progress {
  background-color: #0099FF;
  border-radius: 9999px;
  min-height: 12px;
}

statuspage > scrolledwindow > viewport > box > .icon {
  color: #0099FF;
}

statuspage .title {
  color: #e2e8f0;
  font-weight: 700;
}

statuspage .description {
  color: #7a8ba8;
}

toast {
  background-color: #0a0e1a;
  color: #e2e8f0;
  border: 1px solid rgba(0, 153, 255, 0.22);
  border-radius: 14px;
}

/* ───────────────────────────────────────────────────────────── */
/* Forge GUI 2026-05-25 — header + step indicator */

.forge-header {
  background-color: #0a0e1a;
  border-bottom: 1px solid rgba(0, 153, 255, 0.12);
  padding: 4px 12px;
  min-height: 56px;
}

/* Adw.NavigationView auto-injected back-arrow — render the visible
 * circle on the IMAGE CHILD, not the button itself.
 *
 * GTK4's layout engine constrains the button's geometry via parent
 * container negotiation, and CSS on the button (no max-width, no
 * aspect-ratio, no all:unset, no `.circular`) cannot override it —
 * the button stays an oval. The only CSS-only fix is to make the
 * button an invisible wrapper and render the entire visible chrome
 * on the IMAGE child, which is a leaf widget whose explicit
 * dimensions GTK honors directly. Pattern proved out in our internal
 * theme overrides for the same windowcontrols min/max/close shape;
 * adapted here for button.back.
 *
 * Applies to both the back button (`button.back`, from Adw.NavigationView
 * via libadwaita 1.8.4's adw-back-button.c:518) and any future generic
 * circular / image-only buttons we add. No !important — JS-binding
 * parsers can choke on it for certain properties. */

button.back,
button.back:hover,
button.back:active,
button.back:focus,
button.back:checked,
button.back:focus-visible,
button.circular,
button.image-button {
  padding: 0;
  margin: 0;
  min-width: 0;
  min-height: 0;
  background: none;
  background-color: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  outline: none;
  color: #c4cfe0;
}

button.back > image,
button.back:hover > image,
button.back:active > image,
button.back:focus > image,
button.circular > image,
button.image-button > image {
  min-width: 28px;
  min-height: 28px;
  padding: 6px;
  margin: 4px;
  border-radius: 999px;
  border: 1px solid rgba(0, 153, 255, 0.18);
  background-color: rgba(15, 21, 37, 0.75);
  color: #c4cfe0;
  -gtk-icon-size: 14px;
}

button.back:hover > image,
button.circular:hover > image,
button.image-button:hover > image {
  background-color: rgba(0, 153, 255, 0.40);
  border-color: rgba(0, 153, 255, 0.60);
  color: #ffffff;
}

button.back:active > image,
button.back:checked > image,
button.circular:active > image,
button.image-button:active > image {
  background-color: rgba(0, 153, 255, 0.55);
  border-color: rgba(0, 153, 255, 0.80);
  color: #ffffff;
}

.forge-header-wordmark {
  margin: 4px 8px;
}

.forge-step-indicator {
  padding: 0 12px;
}

.forge-step-dot-active {
  color: #0099FF;
  font-size: 1.0em;
  font-weight: 700;
  padding: 0 1px;
}

.forge-step-dot-inactive {
  color: #3d4f6a;
  font-size: 1.0em;
  font-weight: 500;
  padding: 0 1px;
}

.forge-step-dash {
  color: #1f2c44;
  font-size: 0.85em;
  padding: 0 1px;
}

.forge-footer {
  background-color: rgba(10, 14, 26, 0.7);
  border-top: 1px solid rgba(0, 153, 255, 0.08);
  padding: 4px 0;
}

.forge-nav-button {
  min-width: 96px;
  padding: 8px 24px;
}

/* ───────────────────────────────────────────────────────────── */
/* Welcome page — hero treatment */

.forge-welcome-logo {
  opacity: 0.95;
}

.forge-welcome-title {
  font-size: 2.2em;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: -0.01em;
}

.forge-welcome-tagline {
  font-size: 1.05em;
  font-weight: 500;
  color: #0099FF;
  letter-spacing: 0.04em;
}

.forge-welcome-body {
  font-size: 1.0em;
  font-weight: 400;
  color: #b6c3d2;
  line-height: 1.55;
}

.forge-welcome-cta {
  font-size: 1.1em;
  font-weight: 600;
  padding: 12px 36px;
  border-radius: 9999px;
  background-color: rgba(0, 153, 255, 0.22);
  border: 1px solid rgba(0, 153, 255, 0.55);
  color: #ffffff;
  min-width: 200px;
}

.forge-welcome-cta:hover {
  background-color: rgba(0, 153, 255, 0.34);
  border-color: rgba(0, 153, 255, 0.80);
}

.forge-welcome-cta:active {
  background-color: rgba(0, 153, 255, 0.45);
}

.forge-welcome-legal {
  font-size: 0.78em;
  color: #3d4f6a;
  letter-spacing: 0.05em;
  margin-bottom: 8px;
}

/* ───────────────────────────────────────────────────────────── */
/* Adw.PreferencesPage / Group / Row hierarchy
 *
 * libadwaita defaults render PreferencesGroup titles in a near-text
 * tone against light surfaces, and rows with white-ish backgrounds.
 * On our dark void backdrop those fall through to nearly-invisible.
 * Force the hierarchy onto our palette: bright title, readable dim
 * description, dark card-tone rows, ECG-blue accents on borders. */

preferencespage,
preferencespage > scrolledwindow > viewport > clamp > box {
  background-color: transparent;
}

preferencesgroup {
  margin: 8px 0 20px 0;
}

preferencesgroup > box > label.heading {
  font-size: 1.15em;
  font-weight: 700;
  color: #ffffff;
  margin-bottom: 4px;
}

preferencesgroup > box > label.description,
preferencesgroup > box > label.body,
preferencesgroup .description {
  color: #b6c3d2;
  font-size: 0.92em;
  margin-bottom: 8px;
}

/* Boxed-list rows (the canonical "card with rounded corners" container
 * for PreferencesGroup children). */
list.boxed-list,
listbox.boxed-list {
  background-color: #0f1525;
  border: 1px solid rgba(0, 153, 255, 0.18);
  border-radius: 12px;
  padding: 0;
}

list.boxed-list > row,
listbox.boxed-list > row,
list.boxed-list > .row,
listbox.boxed-list > .row {
  background-color: transparent;
  color: #e2e8f0;
  border-bottom: 1px solid rgba(0, 153, 255, 0.06);
  padding: 12px 14px;
  min-height: 48px;
}

list.boxed-list > row:last-child,
listbox.boxed-list > row:last-child {
  border-bottom: none;
}

list.boxed-list > row:hover,
listbox.boxed-list > row:hover {
  background-color: rgba(0, 153, 255, 0.06);
}

/* ActionRow + ComboRow title + subtitle inside boxed-list */
row > box > box > label.title,
row > box > label.title,
.action-row label.title,
.combo-row label.title {
  color: #e2e8f0;
  font-weight: 600;
  font-size: 1.0em;
}

row > box > box > label.subtitle,
row > box > label.subtitle,
.action-row label.subtitle,
.combo-row label.subtitle {
  color: #7a8ba8;
  font-size: 0.88em;
}

/* ComboRow's selected-value display + dropdown arrow */
combobox > box > stack > label,
.combo-row > box > stack > label,
row.combo > box > stack > label {
  color: #b6c3d2;
}

/* Inside ActionRows/ComboRows, entry widgets should adopt the card-
 * tone background instead of looking like floating windows. */
row entry,
row passwordentry,
.action-row entry,
.action-row passwordentry {
  background-color: #050810;
  border: 1px solid rgba(0, 153, 255, 0.18);
  border-radius: 8px;
  padding: 6px 10px;
  color: #e2e8f0;
  caret-color: #0099FF;
  min-width: 240px;
}

row entry:focus,
row passwordentry:focus,
.action-row entry:focus,
.action-row passwordentry:focus {
  border-color: rgba(0, 153, 255, 0.55);
}

/* The popover that ComboRow opens for selection */
popover.combo-popover,
popover.menu {
  background-color: #0a0e1a;
  border: 1px solid rgba(0, 153, 255, 0.30);
  border-radius: 10px;
}

popover.combo-popover listview row,
popover.combo-popover listview row > box > label {
  color: #e2e8f0;
}

popover.combo-popover listview row:selected,
popover.combo-popover listview row:hover {
  background-color: rgba(0, 153, 255, 0.18);
}

/* ───────────────────────────────────────────────────────────── */
/* Timezone map widget — promoted to hero position on the
 * KeyboardLocale page (360px tall, the visual centerpiece).
 * Subtle inner-glow border picks up the ECG palette. */

.forge-timezone-map {
  background-color: #050810;
  border: 1px solid rgba(0, 153, 255, 0.35);
  border-radius: 14px;
  margin: 6px 0;
  box-shadow:
    0 0 0 1px rgba(0, 153, 255, 0.06) inset,
    0 8px 32px rgba(0, 153, 255, 0.08);
}

/* ───────────────────────────────────────────────────────────── */
/* KeyboardLocale page — hero "region" summary card
 *
 * Sits at the top of the page. Two-column layout: giant flag emoji
 * on the left (acts like a profile photo), rich plain-English summary
 * on the right with a ticking clock at the bottom. Each user-visible
 * field gets its own class so type rank stays consistent with the
 * Welcome page's hero treatment. */

.forge-region-hero {
  background:
    linear-gradient(135deg,
      rgba(0, 153, 255, 0.10) 0%,
      rgba(0, 153, 255, 0.02) 60%,
      transparent 100%),
    #0a0e1a;
  border: 1px solid rgba(0, 153, 255, 0.30);
  border-radius: 16px;
  padding: 22px 26px;
  box-shadow:
    0 0 0 1px rgba(0, 153, 255, 0.06) inset,
    0 12px 40px rgba(0, 153, 255, 0.10);
}

.forge-region-flag {
  /* The giant flag emoji acts as the "avatar" of the region card.
   * Color emojis render through Noto Color Emoji at the requested
   * size — fontconfig fallback handles the glyph lookup. */
  font-size: 76px;
  /* Subtle soft drop-shadow so the flag reads as an object on the
   * card surface, not as flat clipart. */
  text-shadow: 0 4px 16px rgba(0, 0, 0, 0.55);
}

.forge-region-native {
  /* The language's name for itself — biggest typography on the card.
   * This is the "wow, it recognized me" moment. */
  font-size: 1.9em;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: -0.01em;
}

.forge-region-locale {
  /* English form + raw locale id — secondary, dimmer. */
  font-size: 0.95em;
  font-weight: 500;
  color: #7a8ba8;
  letter-spacing: 0.01em;
}

.forge-region-location {
  /* "🗺  Chicago, United States" — set via Pango markup in code. */
  font-size: 1.05em;
  font-weight: 500;
  color: #b6c3d2;
}

.forge-region-clock {
  /* Ticking HH:MM:SS clock — the alive thing on the card. ECG blue,
   * big and bold. The dimmer "· UTC-5" trails after it via Pango
   * markup with alpha='65%'. */
  font-size: 1.05em;
  color: #0099FF;
  font-feature-settings: "tnum" 1;  /* tabular numerals - no width jitter */
}

.forge-instruction {
  /* Lead-the-user paragraph between the hero region card and the
   * first selector. First-time Linux users have no reason to know
   * the prefs below are interactive — this is the affordance. */
  font-size: 0.98em;
  font-weight: 400;
  color: #b6c3d2;
  line-height: 1.5;
  margin-top: 4px;
  margin-bottom: 8px;
}

/* Pango links inside .forge-instruction labels (e.g. PackagesPage's
 * "CLICK HERE") focus the parent Gtk.Label on click — GTK4 then
 * draws the default focus outline around the entire paragraph,
 * which reads as a distracting box after the user just clicked
 * the link with a mouse. Suppress the outline for mouse-driven
 * focus (`:focus`) but keep it for keyboard-driven focus
 * (`:focus-visible`) so Tab-key navigation still shows the indicator.
 * Same pattern modern web browsers adopted for the same complaint. */
.forge-instruction:focus:not(:focus-visible),
.forge-instruction:focus:not(:focus-visible) link {
  outline: none;
  box-shadow: none;
}

.forge-instruction:focus-visible {
  outline: 2px solid rgba(0, 153, 255, 0.40);
  outline-offset: 2px;
  border-radius: 4px;
}

/* Pango <a href> inside our labels — match the ECG-blue accent so
 * links read as InterGenOS-branded, not the generic theme color. */
.forge-instruction link {
  color: #0099FF;
  text-decoration: underline;
}

.forge-instruction link:hover {
  color: #66bfff;
}

/* ───────────────────────────────────────────────────────────── */
/* DiskPage — hero "Destination" card + disk-shape row icons +
 * passphrase strength feedback styling. Same visual vocabulary as
 * the KeyboardLocale hero card (shock-and-awe Wave-3 bar). */

.forge-destination-hero {
  background:
    linear-gradient(135deg,
      rgba(0, 153, 255, 0.10) 0%,
      rgba(0, 153, 255, 0.02) 60%,
      transparent 100%),
    #0a0e1a;
  border: 1px solid rgba(0, 153, 255, 0.30);
  border-radius: 16px;
  padding: 22px 26px;
  box-shadow:
    0 0 0 1px rgba(0, 153, 255, 0.06) inset,
    0 12px 40px rgba(0, 153, 255, 0.10);
}

.forge-destination-icon {
  /* The disk-shape Adwaita symbolic icon, rendered at 76px. Uses the
   * theme's accent color via -gtk-icon-source ; we tint via color
   * here so the icon reads ECG-blue against the card. */
  color: #0099FF;
  -gtk-icon-size: 76px;
}

.forge-destination-path {
  font-size: 1.6em;
  font-weight: 700;
  color: #ffffff;
  font-family: 'JetBrains Mono', 'Inter', monospace;
  letter-spacing: -0.005em;
}

.forge-destination-subtitle {
  font-size: 0.95em;
  font-weight: 500;
  color: #7a8ba8;
}

.forge-destination-size {
  font-size: 1.3em;
  font-weight: 600;
  color: #b6c3d2;
  font-feature-settings: "tnum" 1;
}

.forge-destination-warning {
  /* Red destructive strip baked into the hero card so the
   * "will be erased" message is impossible to miss while looking at
   * the selection. */
  background-color: rgba(239, 68, 68, 0.10);
  border: 1px solid rgba(239, 68, 68, 0.45);
  border-radius: 10px;
  padding: 10px 16px;
  color: #fca5a5;
  font-size: 1.0em;
  letter-spacing: 0.02em;
  margin-top: 4px;
}

.forge-disk-row-icon {
  /* Small disk-shape icon used as the prefix inside each detected-disk
   * row in the Destination PreferencesGroup. */
  color: #0099FF;
  margin-right: 6px;
}

.forge-confirm-destructive check {
  /* Confirm-destructive checkbox — red accent + enlarged so the
   * "I understand" step reads as a deliberate destructive ack, not a
   * passive option. */
  border-color: rgba(239, 68, 68, 0.75);
  border-width: 2px;
  min-width: 22px;
  min-height: 22px;
}

.forge-confirm-destructive:checked check {
  background-color: #ef4444;
  border-color: #ef4444;
}

/* The whole "I understand — erase this disk" row, tinted red so the
 * destructive gate is impossible to miss (GBC001.5: users didn't notice
 * the plain row until 'Next' was blocked). Mirrors the .forge-strength-warn
 * specificity pattern, which renders correctly inside the boxed list. */
.forge-confirm-row {
  background-color: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.50);
  border-radius: 10px;
}

.forge-confirm-row label.title {
  color: #fca5a5;
  font-weight: 700;
}

/* Always-visible EXPERIMENTAL badge at the top of the encryption group —
 * amber, prominent (GBC001.5: the prior "(EXPERIMENTAL)" title text was
 * non-descript). */
.forge-experimental-badge {
  background-color: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.50);
  border-radius: 10px;
}

.forge-experimental-badge label.title {
  color: #fbbf24;
  font-weight: 700;
}

.forge-strength-row {
  /* Base styling for the LUKS passphrase-strength feedback row.
   * Subtle until the user types; the warn/ok modifier classes
   * recolor below. */
  background-color: rgba(0, 153, 255, 0.04);
  border: 1px solid rgba(0, 153, 255, 0.18);
  border-radius: 10px;
}

.forge-strength-warn {
  background-color: rgba(245, 158, 11, 0.10);
  border-color: rgba(245, 158, 11, 0.45);
}

.forge-strength-warn label.title {
  color: #fbbf24;
}

.forge-strength-ok {
  background-color: rgba(16, 185, 129, 0.10);
  border-color: rgba(16, 185, 129, 0.40);
}

.forge-strength-ok label.title {
  color: #34d399;
}

/* ───────────────────────────────────────────────────────────── */
/* UserPage — "Account" hero card. Same visual vocabulary as the
 * Region (KeyboardLocale) and Destination (Disk) hero cards. */

.forge-account-hero {
  background:
    linear-gradient(135deg,
      rgba(0, 153, 255, 0.10) 0%,
      rgba(0, 153, 255, 0.02) 60%,
      transparent 100%),
    #0a0e1a;
  border: 1px solid rgba(0, 153, 255, 0.30);
  border-radius: 16px;
  padding: 22px 26px;
  box-shadow:
    0 0 0 1px rgba(0, 153, 255, 0.06) inset,
    0 12px 40px rgba(0, 153, 255, 0.10);
}

.forge-account-avatar {
  /* Big user-default-symbolic at 76px on the left of the hero card.
   * ECG-blue tinted via color so it reads as an object on the card. */
  color: #0099FF;
  -gtk-icon-size: 76px;
}

.forge-account-identity {
  /* Live `username@hostname` — typography rank-0 on the card. Monospace
   * so technical identifiers read cleanly. */
  font-size: 1.6em;
  font-weight: 700;
  color: #ffffff;
  font-family: 'JetBrains Mono', 'Inter', monospace;
  letter-spacing: -0.005em;
}

.forge-account-subtitle {
  font-size: 0.95em;
  font-weight: 500;
  color: #7a8ba8;
}

.forge-account-badges {
  /* Status-badge row at the bottom of the card. Markup colors handle
   * per-badge state; this is the container styling. */
  font-size: 0.92em;
  font-weight: 500;
  margin-top: 6px;
}

/* ───────────────────────────────────────────────────────────── */
/* Inline documentation viewer — Adw.Dialog rendering markdown
 * as Pango. Used by UserPage MOK row's "First-boot walkthrough"
 * link; intentionally generic so any future doc viewer reuses it. */

.forge-doc-body {
  font-family: 'Inter', 'Cantarell', sans-serif;
  font-size: 1.02em;
  color: #e2e8f0;
  line-height: 1.6;
}

/* Activatable docs-link row inside a PreferencesGroup — strong visual
 * affordance so first-time users see "this is clickable" without
 * having to inspect the row. Subtle ECG-blue tint + ECG-blue title +
 * brighter chevron at rest; full hover glow on mouseover. The leading
 * book icon and trailing chevron both pick up the accent. */

.forge-docs-link {
  background-color: rgba(0, 153, 255, 0.06);
  border: 1px solid rgba(0, 153, 255, 0.22);
}

.forge-docs-link:hover {
  background-color: rgba(0, 153, 255, 0.14);
  border-color: rgba(0, 153, 255, 0.45);
}

.forge-docs-link label.title {
  color: #0099FF;
  font-weight: 600;
}

.forge-docs-link-icon,
.forge-docs-link-chevron {
  color: #0099FF;
}

.forge-docs-link:hover .forge-docs-link-icon,
.forge-docs-link:hover .forge-docs-link-chevron {
  color: #ffffff;
}

/* ───────────────────────────────────────────────────────────── */
/* PackagesPage — "Software selection" hero card + SSH key card.
 * Same hero vocabulary as Region (KeyboardLocale) / Destination
 * (Disk) / Account (User). */

.forge-packages-hero {
  background:
    linear-gradient(135deg,
      rgba(0, 153, 255, 0.10) 0%,
      rgba(0, 153, 255, 0.02) 60%,
      transparent 100%),
    #0a0e1a;
  border: 1px solid rgba(0, 153, 255, 0.30);
  border-radius: 16px;
  padding: 22px 26px;
  box-shadow:
    0 0 0 1px rgba(0, 153, 255, 0.06) inset,
    0 12px 40px rgba(0, 153, 255, 0.10);
}

.forge-packages-icon {
  color: #0099FF;
  -gtk-icon-size: 76px;
}

.forge-packages-count {
  /* Live "N of M groups · K services" — typography rank-0 on this
   * card. Monospace + tabular numerals so the digits don't jitter
   * as toggles flip. */
  font-size: 1.6em;
  font-weight: 700;
  color: #ffffff;
  font-family: 'JetBrains Mono', 'Inter', monospace;
  font-feature-settings: "tnum" 1;
  letter-spacing: -0.005em;
}

.forge-packages-subtitle {
  font-size: 0.95em;
  font-weight: 500;
  color: #7a8ba8;
}

.forge-packages-badges {
  font-size: 0.92em;
  font-weight: 500;
  margin-top: 6px;
}

/* SSH public key card — the ScrolledWindow wrapping the multi-line
 * TextView sits inside an Adw.ActionRow suffix; give it a clear
 * frame so it reads as an editable field, not just dead space. */

.forge-ssh-key-card {
  background-color: #050810;
  border: 1px solid rgba(0, 153, 255, 0.22);
  border-radius: 8px;
  padding: 4px;
}

.forge-ssh-key-card textview {
  background-color: transparent;
  color: #e2e8f0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.92em;
  caret-color: #0099FF;
}

.forge-ssh-key-card:focus-within {
  border-color: rgba(0, 153, 255, 0.55);
}

/* ───────────────────────────────────────────────────────────── */
/* ConfirmPage — "Ready to install" hero card + summary-row icons.
 * Same hero vocabulary as the prior Wave-3 pages; warning strip
 * mirrors the Disk page's destructive treatment because this page
 * is also a destructive-commit gate. */

.forge-confirm-hero {
  background:
    linear-gradient(135deg,
      rgba(0, 153, 255, 0.10) 0%,
      rgba(0, 153, 255, 0.02) 60%,
      transparent 100%),
    #0a0e1a;
  border: 1px solid rgba(0, 153, 255, 0.30);
  border-radius: 16px;
  padding: 22px 26px;
  box-shadow:
    0 0 0 1px rgba(0, 153, 255, 0.06) inset,
    0 12px 40px rgba(0, 153, 255, 0.10);
}

.forge-confirm-icon {
  color: #0099FF;
  -gtk-icon-size: 76px;
}

.forge-confirm-title {
  font-size: 1.9em;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: -0.01em;
}

.forge-confirm-subtitle {
  font-size: 1.0em;
  font-weight: 500;
  color: #7a8ba8;
}

.forge-confirm-warning {
  /* Red destructive strip baked into the hero — same shape as the
   * Disk page's hero warning so the "wipe the disk" disclosure
   * reads consistent across the destructive-commit pages. */
  background-color: rgba(239, 68, 68, 0.10);
  border: 1px solid rgba(239, 68, 68, 0.45);
  border-radius: 10px;
  padding: 10px 16px;
  color: #fca5a5;
  font-size: 1.0em;
  letter-spacing: 0.02em;
  margin-top: 4px;
}

.forge-confirm-notice {
  /* Gold review-notice strip in the hero, ABOVE the red warning —
   * caution tier, not destructive: points the user at a choice on
   * this page (e.g. the default-boot switch) that deserves a look
   * before committing. Same strip shape as the warning so the two
   * read as one severity ladder. */
  background-color: rgba(234, 179, 8, 0.10);
  border: 1px solid rgba(234, 179, 8, 0.45);
  border-radius: 10px;
  padding: 10px 16px;
  color: #fde68a;
  font-size: 1.0em;
  letter-spacing: 0.02em;
  margin-top: 4px;
}

.forge-confirm-row-icon {
  /* Small leading icon on each summary row — ECG-blue tint so the
   * rows scan quickly + match the Wave-3 palette. */
  color: #0099FF;
  margin-right: 6px;
}

/* ───────────────────────────────────────────────────────────── */
/* ProgressPage — live backend log terminal pane. Streams
 * journalctl --follow -u forge-installer-backend.service so the
 * user sees actual install activity (vs. a spinner-without-evidence). */

.forge-backend-log-card {
  background-color: #050810;
  border: 1px solid rgba(0, 153, 255, 0.30);
  border-radius: 10px;
  padding: 0;
  box-shadow:
    0 0 0 1px rgba(0, 153, 255, 0.06) inset;
}

.forge-backend-log,
.forge-backend-log text {
  background-color: #050810;
  color: #b6c3d2;
  font-family: 'JetBrains Mono', 'Noto Sans Mono', monospace;
  font-size: 0.85em;
  padding: 8px 12px;
  caret-color: transparent;
}

/* ───────────────────────────────────────────────────────────── */
/* Forge form sections — plain-Gtk replacement for Adw.PreferencesGroup
 * after the 2026-05-25 Adw-compound-widget pointer-grab regression. */

.forge-section-title {
  font-size: 1.15em;
  font-weight: 700;
  color: #ffffff;
}

.forge-section-description {
  color: #b6c3d2;
  font-size: 0.92em;
  margin-bottom: 4px;
}

.forge-form-row {
  padding: 6px 0;
}

.forge-form-label {
  color: #e2e8f0;
  font-weight: 500;
  font-size: 0.95em;
}

.forge-dropdown {
  background-color: #0f1525;
  color: #e2e8f0;
  border: 1px solid rgba(0, 153, 255, 0.22);
  border-radius: 8px;
  padding: 4px 8px;
  min-height: 32px;
}

.forge-dropdown > button {
  background-color: transparent;
  border: none;
  padding: 4px 8px;
}

.forge-dropdown > button:hover {
  background-color: rgba(0, 153, 255, 0.10);
}
"""


def apply_intergenos_style(display=None):
    """Install the InterGenOS CSS provider on the GDK display.

    Called once at application activate; the provider is registered with
    APPLICATION priority so it overrides theme defaults but stays below
    user-supplied gtk.css overrides.

    Returns the provider so callers can hold a reference (preventing GC)
    and/or replace it later. Returns None if no display is available
    (headless smoke-test path).
    """
    if display is None:
        display = Gdk.Display.get_default()
    if display is None:
        return None

    provider = Gtk.CssProvider()
    try:
        provider.load_from_string(_INTERGENOS_CSS)
    except (AttributeError, TypeError):
        css_bytes = _INTERGENOS_CSS.encode("utf-8")
        provider.load_from_data(css_bytes, len(css_bytes))

    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    return provider
