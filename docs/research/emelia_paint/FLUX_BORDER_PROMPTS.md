# Emelia's Magical Studio — FLUX Border Prompts

Generate at 2048x2732 (iPad Pro portrait). Each border should have a soft, 
light center (~80% of canvas) that fades into illustrated edges. Watercolor 
style, kid-friendly, warm palette. The center must be light enough to draw 
on top of.

---

## 1. Jungle Paradise
```
A soft watercolor children's illustration frame border for a drawing app.
Lush tropical jungle edges with friendly cartoon vines, oversized leaves,
and exotic flowers creeping in from all four sides. A tiny toucan peeks
from the top-left corner, a baby monkey hangs from a vine in the top-right.
Bottom edge has soft ferns and a small friendly frog. The center is open,
very light cream/white, fading gradually from the illustrated edges inward.
Dreamy, warm, pastel jungle greens, soft yellows, gentle pinks. Children's
book illustration style. No text.
```

## 2. Enchanted Forest
```
A soft watercolor children's illustration frame border for a drawing app.
Magical forest edges with whimsical trees, mushrooms with tiny glowing
spots, and delicate wildflowers along the bottom. A friendly owl peeks
from a hollow in the top-right tree. Fireflies (soft dots of light)
scattered along the edges. Left side has a gentle deer peeking in. Soft
moss and small ferns along the bottom. The center is open, very light
cream/white, fading from edges inward. Enchanted storybook atmosphere,
muted greens, warm ambers, soft lavenders. Children's book illustration
style. No text.
```

## 3. Waterfall Cove
```
A soft watercolor children's illustration frame border for a drawing app.
A gentle waterfall cascading down the left edge into a serene pool at the
bottom-left corner. Smooth river stones and water lilies along the bottom
edge. Tropical plants and soft ferns frame the right side. A small rainbow
arcs through mist near the top of the waterfall. A tiny friendly fish
jumps from the pool. Birds in soft silhouette near the top. The center is
open, very light cream/white with the faintest blue tint. Soft teals,
gentle greens, warm golds, misty whites. Dreamy and peaceful. Children's
book illustration style. No text.
```

## 4. Underwater Kingdom
```
A soft watercolor children's illustration frame border for a drawing app.
Coral reef edges with friendly sea creatures peeking in from all sides.
Bottom edge has colorful coral, sea anemones, and small starfish. A smiling
cartoon octopus waves from the bottom-right corner. A seahorse floats
along the left edge. Small tropical fish swim near the top. Gentle bubble
trails rise along the edges. Seaweed sways softly on both sides. The
center is open, very light aqua/white, like looking through clear water.
Soft corals, pastel purples, gentle turquoise, warm peach tones.
Children's book illustration style. No text.
```

## 5. Fairy Garden
```
A soft watercolor children's illustration frame border for a drawing app.
Whimsical garden edges with oversized pastel flowers, tiny toadstools, and
delicate butterfly trails. A small fairy house (acorn-shaped door) nestles
in flowers at the bottom-left. Gentle butterflies flutter near the top
corners. Ladybugs on leaves along the right edge. Dandelion seeds float
across the top. Soft sparkle dots scattered throughout the border edges.
The center is open, very light pink-white, fading from the garden edges
inward. Pastel pinks, soft lavenders, gentle mint greens, warm yellows.
Magical and cozy. Children's book illustration style. No text.
```

## 6. Starry Night Sky
```
A soft watercolor children's illustration frame border for a drawing app.
Night sky edges with a crescent moon in the top-left corner, soft glowing
stars scattered along the top and sides. Gentle clouds drift along the
bottom edges. A small sleeping cat curls on a cloud in the bottom-right.
A friendly owl sits on a branch in the top-right. Shooting stars trail
across corners. The center is open, very light periwinkle/white — light
enough to draw on but with a subtle night-sky warmth. Soft navy fading
to lavender, warm gold stars, gentle silver moonlight. Cozy bedtime
atmosphere. Children's book illustration style. No text.
```

---

---

# CUSTOM STICKERS — Batch of 8 per sheet

Same style anchor for all: "soft watercolor children's book illustration,
transparent background, single character/object, cute and friendly"

## Sticker Sheet 1 — Animals
```
A grid of 8 cute watercolor animal characters for a children's drawing app,
each in its own cell on a transparent background. Friendly and round:
baby elephant, baby giraffe, baby penguin, baby lion, baby whale, baby
ladybug, baby hedgehog, baby sloth. Each animal is simple, expressive,
with big eyes and a gentle smile. Soft watercolor style matching children's
book illustration. Pastel tones, warm and cozy. PNG with transparency.
```

## Sticker Sheet 2 — Nature & Magic
```
A grid of 8 cute watercolor objects for a children's drawing app, each
in its own cell on a transparent background: rainbow with clouds,
magic wand with sparkles, treasure chest, friendly mushroom house,
hot air balloon, crystal gem, magic potion bottle, crown with jewels.
Each object is simple and round, soft watercolor children's book
illustration style. Pastel tones. PNG with transparency.
```

## Sticker Sheet 3 — Food & Treats
```
A grid of 8 cute watercolor food characters for a children's drawing app,
each in its own cell on a transparent background: smiling ice cream cone,
happy donut, friendly slice of watermelon, cute cupcake with face,
adorable cookie, cheerful lollipop, sweet strawberry with eyes, happy
slice of pizza. Kawaii-inspired but watercolor style. Soft pastels.
PNG with transparency.
```

---

# UI ICONS — Same watercolor style

## Clear/Trash Button
```
A cute watercolor illustration of a small sparkling trash can for a
children's app icon. The trash can is friendly and round, pastel purple
with golden sparkle stars around it. Soft watercolor children's book
illustration style. Transparent background. Simple, iconic, recognizable
at small sizes. PNG with transparency.
```

## Save Button (for parent controls)
```
A cute watercolor illustration of a small camera with a heart on the lens
for a children's app icon. Soft pink and gold, friendly and round.
Watercolor children's book illustration style. Transparent background.
PNG with transparency.
```

## App Icon (1024x1024)
```
A children's app icon for "Emelia's Magical Studio." A cute watercolor
paintbrush painting a rainbow trail across a soft cream canvas. Sparkles
and tiny stars surround the brush tip. The background is a gentle gradient
from soft lavender to warm pink. A small friendly butterfly sits on the
brush handle. Warm, magical, inviting. Watercolor children's book
illustration style. Square with no rounded corners (iOS adds them).
1024x1024 pixels.
```

---

# POST-PROCESSING

## Borders
1. If center isn't light enough, adjust levels — push center to near-white
2. Save as PNG at 2048x2732
3. Add to Xcode Assets catalog as `border_jungle`, `border_forest`, etc.
4. App overlays the border on top of the drawing canvas

## Stickers
1. Cut each character from the grid into individual PNGs
2. Remove background (should already be transparent from FLUX)
3. Resize to 256x256 each
4. Add to Assets catalog as `sticker_elephant`, `sticker_giraffe`, etc.
5. Replace emoji stickers with Image() views in the app

## UI Icons
1. Trash icon: 128x128, add as `icon_clear`
2. Save icon: 128x128, add as `icon_save`
3. App icon: 1024x1024, add to AppIcon asset set

## Style Consistency
All prompts use "soft watercolor children's book illustration" as the
style anchor. Generate in the same session/seed range if possible to
maximize visual coherence across borders, stickers, and icons.
