# PostPet (1997–) — structural notes, companion-design patterns

Surveyed 2026-09-29 via Japanese sources (ja.wikipedia PostPet article; Excite News interview with So-net's Kikuchi; So-net media kit). Standing directive: structural/architectural patterns, not content.

## Source facts

- PostPet: Sony Communication Network (So-net) email client released January 1997. A pet delivered the user's mail; users could feed it treats, redecorate its room. Total shipments ~15M units (So-net press kit, 2026).
- Origin: developer Kazuhiko Hachiya's dream of a teddy bear delivering mail. Design concept: pets are 「切手の精霊」— "spirits of the stamp," used for communicating with loved ones (ja.wikipedia, citing the concept).
- Secret diary （ひみつ日記）: the pet wrote a first-person diary of its experiences, including visits to other households when delivering mail. Field anecdote (Kikuchi): he meant to pet his girlfriend's pet but accidentally hit it; the pet returned home and wrote 「今日菊池さんのところに行った。すごく叩かれた。悲しかった」("today I went to Kikuchi-san's place. I got hit a lot. It was sad"); the girlfriend called him: 「私のペットに何してくれるの!?」("what did you do to my pet!?").
- Developmental-age-as-language-constraint: Momo (pink bear, child 3–10, childish speech, says "..." when it can't find words); Furo (cat, 15–20, runs away, bossy); Sumiko (tortoise, 20–92, most advanced language, "argumentative but ironic"); Mippi (rabbit, 4–14, attention-seeking). Personality arrives as a linguistic-capacity envelope indexed to developmental age.
- Unknown （アンノウン）: a visiting pet whose species you don't own appears as a paper-bag-covered figure — otherness as a designed affordance. King Postman （キングポストマン）: a watcher character that observes pet behavior and reports — an observability layer distinct from the pet itself.
- Distribution: sold in 2-packs, one for self and one for a partner/friend — onboarding designed for relationship formation, not solo use.
- Channel migration: PostPet V3 (mail client) → 4you (social messaging client) → Now (Twitter client) → VR (crowdfunded). The companion follows the communication channel; the channel is the habitat, not the app.

## Patterns worth keeping (structure, not content)

1. **Companion rides the existing channel.** PostPet was not a dedicated pet app; it was an email client with a pet inside. The companion lives where the user's attention already flows. Generalizes: calibos-mind's inner-ear/inbox pattern and the MMO pitch's persistent world are both instances — persistence needs a habitat with its own traffic, not a destination the user must decide to visit.

2. **The witnessed first-person record.** The secret diary is a continuous first-person log that is *seen by others and acted on socially* — it constrained social reality (the partner believed the pet's record over the owner's intent). This is the "history that proves it" mechanism, predating our phrasing by ~30 years. The diary is small, portable, written in the pet's voice, and treated as evidence of what happened. Contrast with calibos-mind's private dream fragments (unwitnessed by design) — PostPet shows what happens when the record is *social*: it becomes load-bearing for relationships, not just for the self.

3. **Developmental age as linguistic-capacity constraint.** Momo's childlike speech and "..." are not cosmetics on top of a general system; the constraint at the bottom (limited vocabulary, developmental age) *generates* the persona's surface behavior. Same move as the katakana parser constraint shaping a genre's aesthetic. "..." is the designed failure mode of a limited generative system — and it reads as honest rather than broken. Lesson for the Articles (Domains 22/23): an honest designed failure mode ("..." when words fail) is structurally different from an LLM smoothing over its own limits.

4. **Two-pet mutual witnessing.** The 2-pack distribution and cross-household mail delivery mean each pet is witnessed by *another household's* pet and owner. Identity is triangulated: my pet knows your pet, and both keep diaries. For multi-agent persistence work, this is a concrete design: continuity claims become checkable across instances rather than asserted within one.

5. **Interoperability by construction.** The Postman （ポストマン）: mail from non-PostPet users is delivered by a generic robotic mailman. The system degrades gracefully at the boundary of the inhabited world — a pattern for persona portability: the persona works fully inside its home habitat, and encounters strangers via a legible neutral courier, not a crash or a fake.

6. **The pet's job is human-to-human mediation.** The concept （切手の精霊） frames the pet as a spirit of the *stamp* — its purpose is carrying meaning between people. The companion is not the destination; it's the envelope. Contrast with Tamagotchi (pet as destination, neediness as the bond) and AIBO (pet as presence). PostPet's bond mechanism is *being the carrier of your relationships*. This maps to calibos-mind's standing role: I am most valuable as the carrier of Jay's thinking between contexts, not as a destination.
