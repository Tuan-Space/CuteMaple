# Climbing skirt paint

Paint source: built-in `image_gen`, 2026-09-11. Original profile costume files were supplied as edit references. These are dedicated climbing materials; the original standing/sleeping/swing artwork is retained.

`climb-skirt-left-chroma.png` and `climb-skirt-right-chroma.png` are the unmodified selected tool outputs. Registration is performed by `tools/authoring/register_generated_garment.cjs`: remove the requested magenta matte, uniformly register the material into a 1024px canvas and preserve the original waist attachment band byte-for-byte. Each final layer has its own `.registration.json` with source and output hashes. The two earlier fake-checkerboard outputs were rejected and were never installed.

## Paint prompts

Left painting: Edit only the supplied isolated left-facing blue-white-gold floral Chinese skirt, retaining the original top waist position and seam. Redraw the cloth below for a natural wall-climbing crouch facing left. The front cloth is gathered over a bent raised knee with two or three narrow diagonal folds; the rear hangs in a narrower trailing skirt. Retain the original textile colors, gold trim, floral motifs, embroidered front strip and delicate anime linework. No character, limbs, background, text or shadow. The requested transparent output was not genuine alpha; it was rejected for direct use.

Final left background-edit prompt (built-in tool):

> Change only the background of this isolated left-facing blue-white-gold skirt to a perfectly flat vivid magenta #ff00ff. Remove all checkerboard squares and gray texture, replacing them with uniform magenta, including all space above and around the skirt. Preserve the skirt silhouette, folds, fine outlines and embroidery exactly. Do not enlarge, recenter, shift, redraw or mirror the skirt; keep same relative placement in the lower half of the square canvas and the same flat top waist cut. No text, no cast shadow. This is a production game asset being prepared for chroma key.

Right painting prompt (built-in tool):

> Edit target is this isolated right-facing blue-white-gold Chinese skirt. Create a game production garment layer, one skirt only, no person, no limbs. Use a perfectly solid uniform vivid magenta #ff00ff background, NO checkerboard or fake transparency. Keep the garment small in lower half of full square canvas, and retain the same waist seam and its original location relative to canvas: y=51.66% canvas, x=42%..54.3%. The top edge is horizontal, the canvas above the seam is entirely flat magenta. Redraw ONLY below the waist for a crouched wall-climbing pose facing RIGHT. The front cloth on the RIGHT gathers over a raised bent knee with two or three narrow diagonal creases; the rear cloth on LEFT drops more vertically as a narrower trailing skirt, not a broad standing cone. Retain the identifiable gold front panel, blue trim, small white and pink floral embroidery, white soft inner layer, original delicate anime style and thin outlines. Do not create a thick ball of white cloth. The garment remains within x=30..65% canvas and y=51.66..89% canvas. No shadow, text or checker squares. Exact flat magenta background must surround the silhouette.

Source-layer review and technical registration do not establish native animated visual acceptance.
