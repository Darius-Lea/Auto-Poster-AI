# Clip typography and revision controls

Creation controls offer the bundled Inter/Manrope fonts, compact/balanced/large
caption size, lower/center placement and the existing three caption treatments.
These choices reach the real renderer and are recorded in the build metadata.
Caption layout preserves safe-area and hook eye-line constraints. No arbitrary
font files or server paths are accepted.

Ready clips expose **Edit video look and fonts**. Applying confirmed changes
creates a new private job with the same source window, saved transcript,
corrected captions and frozen logo. The parent remains available and no revised
clip is automatically published. Normal render-attempt limits apply.

Validation: three typography adapter tests, fourteen existing adapter tests,
twelve hosted-clip tests and two design-revision tests passed. Real Pillow
caption frames rendered for both fonts and differed visually. JavaScript syntax
and diff checks passed. A complete live Render transcription/design export and
competitive-quality assessment were not verified; no claim of industry-best or
current trend ranking is made.
