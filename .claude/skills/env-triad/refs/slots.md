# What the three slots mean

Paraphrased from ENVO's own guidance, [Using ENVO with MIxS][wiki], which the submission
schema's slot descriptions link to. Read this before choosing a term; [validation.md](validation.md)
covers what happens to it afterwards.

The three slots answer three different questions, at three different grains. A term that
answers one of them usually cannot answer another.

## env_broad_scale — which major environmental system?

The big, contextualising environment: the ecosystem-level answer to "where on Earth was this?"
Use a subclass of **biome** (`ENVO:00000428`).

Not a process, not a material, not an individual object. ENVO warns specifically against
omitting this for microbial samples — working at microscale does not remove the macroscale
context. A soil sample from an Oregon poplar plantation still sits in a forest biome.

## env_local_scale — what was next to the sample?

The entities in the sample's immediate vicinity that plausibly influenced it. ENVO's own
framing is the useful one: **countable things** — "a rock, a snow crystal, a cave, a
hydrothermal vent" — rather than abstract systems.

This should add finer-grained information than `env_broad_scale`. If it repeats the biome, it
is not doing any work.

> `ecosystem [ENVO:01001110]` is the anti-pattern here: it is a system, not a countable thing
> in the vicinity, and it is less specific than the biome above it.

**Why the anchor class is only advisory.** The gate checks descent from `astronomical body
part` (`ENVO:01000813`), but that is a structural stand-in for "countable entity nearby", and
a leaky one. `aquifer`, `farm` and `fen` are all countable, all curated by NMDC, and all fall
outside the anchor. Where the anchor and this rule disagree, this rule is the real one.

## env_medium — what was the sample made of, or sitting in?

The material immediately surrounding or composing the sample at collection. Use a subclass of
**environmental material** (`ENVO:00010483`).

The test is grammatical: a **mass or volume noun** — a mass of soil, a volume of water, an
amount of tissue. Not a countable entity. ENVO names `cuticle`, `microbial mat` and `tree` as
things that fail this test.

That distinction is sharper than it looks, because ENVO often carries both forms:

| countable entity — wrong for env_medium | material — right |
|---|---|
| `microbial mat [ENVO:01000008]` | `microbial mat material [ENVO:01000157]` |
| `biofilm [ENVO:00002034]` | `biofilm material [ENVO:01000156]` |

Reach for the obvious label and you get the wrong one. Look it up.

**Report the material as it was, not as you received it.** Freezing, sieving and homogenising
are sample processing, not environmental context — they belong in the processing fields, and
`env_medium` describes the material before any of that.

## Mistakes ENVO calls out

- **Repeating a term across slots.** Identical entries in two slots mean one of them is not
  answering its question. The gate rejects this; see [validation.md](validation.md).
- **Conflating processing with environment**, as above.
- **Dropping the macroscale context** because the sample is microbial.

## Multiple terms

ENVO's format for several terms in one slot is pipe-separated:
`termLabel [termID]|termLabel [termID]`.

**This pipeline emits one term per slot.** The schema's regex tolerates the piped form only by
accident, and validation reads the whole string as a single label, so a piped value fails with
a confusing message. Choose the single best term. If a sample genuinely needs several, that is
a change to make deliberately rather than by slipping pipes past the gate.

[wiki]: https://github.com/EnvironmentOntology/envo/wiki/Using-ENVO-with-MIxS
