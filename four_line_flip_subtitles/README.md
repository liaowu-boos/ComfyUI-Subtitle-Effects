# Four-Line Flip Subtitle

ComfyUI node: `Four-Line Flip Subtitle (四行翻转字幕)`

This node implements the subtitle flip rules confirmed from the reference video.

## Core Rules

- Subtitles are grouped by 4 lines by default.
- New lines enter one by one.
- The newest line bottom is locked to the horizontal center line.
- When a new line appears, older lines in the current group move upward.
- Old subtitles are cumulative: all previous subtitle groups are kept as one old layer.
- The old layer flips as a whole, not one group at a time.
- Flip direction alternates:
  - group 1 old layer: counterclockwise
  - group 2 old layer: clockwise
  - group 3 old layer: counterclockwise
- Alignment follows the confirmed rule:
  - group 1: left
  - group 2: left
  - group 3: right
  - group 4: left
  - then continues alternating.
- The real flipped old subtitle layer keeps its original colors.
- Gray is only used for motion trail/shadow, never as the real old subtitle color.

## Highlight Rules

Default highlight mode is `auto`.

- Manual highlight has priority:
  - `highlight_words`
  - inline `[word]` or `*word*`
- Auto mode selects at most 1 red word per 4-line group by default.
- A group can have no red word if no strong candidate exists.
- Preferred candidates:
  - technical tokens, numbers, English
  - visual nouns
  - action result words
  - emotional closing phrases
- Weak words such as `今天`, `然后`, `真的`, `一个`, `去了`, `一起` are filtered out.

## Font Size Rule

Default mode is `fit_active`.

- The currently entering line is the active line.
- The active line is enlarged to fit the horizontal safe area.
- Horizontal safe area is controlled by `padding`.
- At 720px width, default padding 84 gives a text box from x=84 to x=636.
- When a new line appears, older lines interpolate to smaller tier sizes and move upward.
- Full 4-line tier ratios are approximately `0.48 / 0.58 / 0.70 / 1.00`.
- This matches the reference behavior where the newest line becomes the largest visual line.

The old fixed-size behavior is still available with `font_layout_mode=fixed`.

## Entry Motion Rule

The production node now uses the confirmed v16 entry-motion engine.

- First line: `slide_from_left`
- Group-boundary first line: `hinge_fade` or `hinge_character_build_reverse`
- Other lines use deterministic, text-stable selection from:
  - `character_build_forward`
  - `character_build_reverse`
  - `tracking_collapse`
  - `fade_rise`
  - `large_to_fit_zoom`
  - `fast_pop`
  - `stack_zoom`
- Existing lines reflow upward before the new line becomes fully visible.
- Per-character animation uses complete glyph layers to avoid clipping thin characters.
- During every group flip, the next group's first line starts entering behind the old subtitle layer.
- The same input always produces the same entry-template sequence.
- The output timeline automatically extends until every text group has been shown.
- The completed old group is attached to the new first line using the old group's last-line logical line-box bottom at the flip-hinge endpoint; accumulated history bounds do not control vertical alignment.
- Once the new first line attaches to the old subtitle, old history and all already-visible lines are rendered as one locked parent layer. Later lines can enter separately, but the existing parent layer moves upward and scales as a single whole.

## Production Baseline

- Production behavior baseline: LAB v16
- Node ID remains `SubtitleFourLineFlip`
- Display name remains `Four-Line Flip Subtitle (四行翻转字幕)`
- Default shadow and motion-trail opacity are `0`, matching the reference.
