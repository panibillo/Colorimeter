# The pad's edge: a model for the bands, and a plan to test it (2026-10-23)

**A plan in concept.  Nothing has been run and no code is proposed.**
Numbers in the examples are illustrations, not measurements.

Symbols follow `tex/chain_derivation.tex`.  Reminders:

- **d** is a pixel's reading with the dark level removed (post-image).  **d′**
  is the same in the pre-image.  The prime always means pre-image.
- **n** is the most light that could return to the pixel.  **β** is the shading
  (1 = fully lit).  **r** is the fraction of arriving light the pad sends back
  (bigger means lighter).  **t** = r / r′ is the fraction the dye lets
  through.  **α** = −log10(t) is the dye on the absorbance scale (bigger
  means darker).
- New in this note: **x**, the distance, in pixels, from a pixel to the pad's
  edge, measured into the pad (x = 0 is the edge); **κ(x)**, the edge factor;
  **ℓ(x)**, the share lost; **ξ**, the share of crossing light that the
  surroundings send back.

---

## 1. The question, and what we will not do

Near the pad's edge the reading falls over about 3 to 5 pixels.  We will
**not** discard the edge pixels.  We will group pad pixels into **bands** by
their distance x from the edge, and give each band its own factor.

---

## 2. The model, one step at a time

**Step 1: deep inside the pad.**  Every photon that enters and is not absorbed
comes back out of the top, because the white plastic backing is opaque.  This
is the chain's equation:

    d = r · β · n                                           (1)

**Step 2: near the edge.**  A photon that enters at distance x from the edge
wanders sideways inside the paper.  Call ℓ(x) the share of the photons that
would have returned to the camera but instead leave through the cut edge.
They leave sideways, not toward the camera.  What lies beyond the edge sends
back a share ξ of them:  ξ ≈ 0 for flat black stage, larger for white
plastic.  The net loss is (1 − ξ)·ℓ(x), so

    d = κ(x) · r · β · n,     κ(x) = 1 − (1 − ξ)·ℓ(x)       (2)

κ is a multiplier like β.  Far from the edge κ = 1 and (2) becomes (1).

**Step 3: the two ends of ℓ.**  Far from the edge no photon reaches it, so
ℓ(∞) = 0.  Exactly at the edge, a photon is as likely to wander outward as
inward, so half are lost: ℓ(0) = ½.  Therefore

    κ(0) = ½·(1 + ξ)                                        (3)

Black surroundings (ξ ≈ 0): the reading at the edge is half of the interior.
White plastic beyond (ξ > 0): less than half is lost.

**Step 4: how ℓ falls from ½ to 0.**  This is what B1, B2 and B3 in the first
draft were: three guesses for the shape of ℓ(x).  The sideways offset u used
there was only bookkeeping and is not needed.

| | ℓ(x) | Why you would expect it |
|---|---|---|
| B1 | ½·e^(−x/L) | Photons are removed at a steady rate per distance traveled sideways (absorbed, or sent out the top). |
| B2 | ½·erfc( x / (σ√2) ) | Many small random steps, as in a thin slab on a backing; the reach is a bell curve. |
| B3 | ½·(1 + x/x₀)^(−p) | A few long flights between fibers; a slow tail. |

All three start at ½ and end at 0.  They differ in how fast, and in how long
the tail is.  On a plot of log ℓ against x, B1 is a straight line, B2 bends
downward, and B3 is straight when x is also on a log scale.

**Step 5: what the camera records.**  The lens and the pixel size smear κ(x)
by about a pixel, so the bands at x < 1 are not clean.  The blur is measured
on a gray chip's edge (section 4, step 3).

---

## 3. What κ does to pre/post pairs, and can it replace registration?

**Step 6: the ratio when the pad has moved.**  A pixel sits at distance x in
the post-image and at x′ = x + s in the pre-image (s = the shift in pixels
perpendicular to the edge).  From (2):

    d / d′ = [ κ(x) / κ(x′) ] · (r / r′) · (n / n′)         (4)

If x′ = x, κ cancels, just as β does in the chain.  If not, a false change
remains.  To first order:

    κ(x) / κ(x + s)  ≈  1 − s · κ′(x) / κ(x)                (5)

**Illustration** (B1, ξ = 0, L = 2 px, s = 0.2 px; the false change in
absorbance is ΔA ≈ 0.434 × the fractional change):

| x (px) | 0.5 | 1 | 2 | 4 |
|---|---|---|---|---|
| κ(x) | 0.61 | 0.70 | 0.82 | 0.93 |
| false change in d / d′ | 6.4% | 4.4% | 2.3% | 0.7% |
| ΔA | 0.028 | 0.019 | 0.010 | 0.003 |

The camera noise is about 0.002 A, so an unnoticed 0.2 px shift is clearly
visible out to about 4 px.  That is the apparent change at the edge.

**Step 7: remove κ instead of lining up.**  Suppose κ is known.  Find x for
each pixel from **its own image's** fitted edge, and divide:

    d / κ(x) = r · β · n          d′ / κ(x′) = r′ · β · n′  (6)

The edge factor is gone without moving either image onto the other.  So your
conjecture holds, with two limits:

1. **It removes only the smooth edge loss.**  The paper's fine texture is part
   of β, and β cancels only if the pixels are lined up (the chain's
   assumption A3).  Lining up is still needed for texture, but an error of
   0.2 px no longer leaves a 4% edge artifact.
2. **κ must be the same in the pre- and post-image.**  Dye absorbs the
   sideways-wandering photons too, so with dye ℓ is smaller than in the
   pre-image.  Dividing a dyed edge by the pre-image's κ then removes too
   much, and the edge looks **less** dyed than it is.  The error is a
   fraction of ℓ, so it is largest at x = 0.  It is small where the edge is
   undyed.  This needs a check, not an assumption (step 4 below).

**Step 8: after κ is removed, what is left?**  On a pad fully exposed to
arsine you see edges both darker and browner.  κ itself is nearly colorless
(paper absorbs little in the visible), so it can darken the edge but should
not make it brown.  After dividing by κ, any darkness or brownness that
remains at the edge is dye (α), which would mean more gas reached the edge,
by a mechanism not yet stated.  If it all vanishes, it was κ.

---

## 4. Plan, in small steps

Each step ends with a figure for you to approve before the next.

1. **Band profile, pre-image.**  One sample, one channel (suggest C1032,
   cyan.B).  For each edge separately (left, right, top, bottom), group pad
   pixels into 1 px bands by x from that image's fitted edge, skipping 10 px
   at each corner.  Plot the band mean of d′, divided by the deep-interior
   mean, against x.  This is κ(x).  Second panel: log ℓ = log(1 − κ) against
   x.  Stacked panels, shared x axis.
2. **Does it match the model's fixed points?**  Read off κ(0) and compare with
   equation (3).  The surroundings differ by edge, which gives a sharp
   prediction:
   - **Vertical edges:** black stage adjacent, ξ ≈ 0, so κ(0) ≈ ½.
   - **Horizontal edges:** white plastic beyond the paper, ξ > 0, so a
     shallower dip.

   If the vertical and horizontal edges do not differ in this order, the
   lost-sideways picture is wrong or incomplete.  The log plot then says which
   of B1, B2, B3 fits.
3. **Instrument blur.**  The same band method on a gray chip's edge, to see
   how much of the first pixel or two is the lens.
4. **Is κ the same in the post-image?**  On pads whose edges were not exposed,
   compare κ(x) from the pre-image with the post-image's.  Agreement within
   0.002 A means equation (6) can use the pre-image's κ.  Then compare the
   dye image near the edge made two ways: lined up (equation 4) and
   κ-corrected (equation 6).  Report the difference in A per band.
5. **Fully exposed pads.**  Apply equation (6), then report the remaining
   edge darkness and its color, in A per band and per channel (step 8).

**Stop rule.**  If step 1 shows κ within 0.002 A of 1 beyond x = 2 px, the edge
is a two-pixel effect, steps 2 to 5 are not worth doing, and we write a short
note.

---

## 5. Other causes, and what they change

All of these enter d as a multiplier that depends on x, so equations (4) and
(6) treat them the same.  They differ in what they teach us.

- **Edge shading (C).**  With lighting from the four corners in every color,
  each vertical edge may be shaded from two of the four LEDs.  This is part of
  β, which depends on LED geometry.  It looks like the surroundings effect in
  step 2 (the two edge types differ for both reasons).  Separating them needs
  shots with one LED at a time, or the pad turned 90° on the stage.
- **Disturbed fibers at the cut edge (D).**  This changes r in the first pixels
  and appears in the pre-image as well.  It cannot be told apart from κ by
  the pre-image alone.  Unlike the sideways loss, it can make the edge
  **lighter**, so a band brighter than the interior would point to D.
- **Stray light (E).**  Ruled out by the black flock-lined box.

---

## 6. What this cannot tell us

- One pad per strip, one paper stock: the κ we measure is for this paper and
  these edges.
- A single band profile cannot separate the sideways loss from edge shading or
  disturbed fibers.  Only the vertical/horizontal prediction, the colors, and
  the sign (lighter or darker) give any separation.
- Each Bayer layer samples a coarser grid, so layers are done one at a time.
  Never average Gr with Gb.  Skip royal_blue and violet at first, because the
  paper fluoresces under violet.

---

## 7. Questions

1. **Pixel size.**  Is "3 to 5 px" in raw pixels or in one Bayer layer's pixels?
   *Recommend:* Bayer-layer pixels, with the millimeter conversion on every
   figure.
2. **Can one LED of a color be fired alone?**  Needed to separate edge shading
   from the sideways loss.  *Recommend:* check before step 2.
3. **Which samples?**  Please name one fully exposed pad and a few
   center-dot pads with unexposed edges.  *Recommend:* C1032 for step 1.
4. **Step 1 only, then stop for your view of the figure?**  *Recommend:* yes.
