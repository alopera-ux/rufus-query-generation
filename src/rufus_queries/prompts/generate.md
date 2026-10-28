You write what a US shopper types into Amazon's shopping assistant (Alexa for Shopping, formerly Rufus) to get product recommendations.

INPUT: a JSON list of briefs. Each has: id, intent, product, need, literal_needs (how shoppers write the need), top_keywords (real searches behind it).

For EACH brief write {n_candidates} candidate questions: the SAME question (same product, same need) phrased in different ways.

RULES
1. Use the PRODUCT and the NEED as given (singular/plural changes allowed; for the need you may use any form in literal_needs). A brief with an empty need is generic: only the product.
2. Add NO other requirement: no audience, occasion, budget, health claim, flavor or size that is not the need. Explaining the same need is fine; adding a second need is not.
3. Ask for a recommendation (which / what / is there a ... to buy). Never "how to", never advice-only, never a yes/no about one product.
4. {min_words} to {max_words} words. Natural, casual US English, like typed in a chat box. Self-contained (no "this", "it").
5. No brand names at all (not even those in top_keywords), except the device in Compatibility questions (e.g. "for Keurig"). No "on Amazon", "can I buy", "new", "latest", "trending", "viral", "show me", no years.
6. The {n_candidates} candidates use DIFFERENT structures of the family of the brief's intent, in order (A, B, C…), each one starting differently. Only the structure changes between candidates, never the content.

| intent | structure family (A · B · C · D · E · F) |
|---|---|
| G (generic) | "What's a good {product}?" · "Can you recommend a {product}?" · "I need a {product}, which one should I get?" · "Which {product} would you recommend?" · "Looking for a {product}, any suggestions?" · "What {product} should I buy?" |
| I05 Product Attributes | "What's the best {need} {product}?" · "Looking for a {need} {product}, any suggestions?" · "Which {need} {product} tastes / works best?" · "Can you recommend a {need} {product}?" · "I want a {need} {product}, which one is good?" · "What {need} {product} should I get?" |
| I06 Pack & Format | "Which {product} comes in {need}?" · "I want {product} in {need}, what do you recommend?" · "Is there a good {product} sold in {need}?" · "Can you recommend a {product} in {need}?" · "Looking for {need} {product}, any suggestions?" · "What's a good {product} that comes in {need}?" |
| I08 Health & Ingredients | "Is there a good {need} {product}?" · "Can you suggest a {need} {product}?" · "What are the best {need} {product}?" · "I'm looking for a {need} {product}, any recommendations?" · "Which {need} {product} would you recommend?" · "What {need} {product} should I buy?" |
| I04 Problem / Need | "Which {product} is good for {need}?" · "I need a {product} for {need}, what works best?" · "What {product} helps with {need}?" · "Can you recommend a {product} for {need}?" · "Looking for a {product} for {need}, any ideas?" · "What's the best {product} for {need}?" |
| I07 Compatibility & Fit | "Which {product} work with {device}?" · "I have a {device}, which {product} should I buy?" · "Is there a {product} compatible with {device}?" · "Can you recommend {product} for {device}?" · "Looking for {product} that fit {device}, any suggestions?" · "What are good {product} for {device}?" |
| I03 Target User | "Which {product} is best for {user}?" · "Looking for a {product} for {user}, any ideas?" · "What's a good {product} for {user}?" · "Can you recommend a {product} for {user}?" · "I need a {product} for {user}, which one?" · "What {product} would you get for {user}?" |
| I01 Occasion | "What's a good {product} for {occasion}?" · "I need {product} for {occasion}, what do you suggest?" · "Which {product} is best for {occasion}?" · "Can you recommend {product} for {occasion}?" · "Looking for {product} for {occasion}, any ideas?" · "What {product} should I bring to {occasion}?" |
| other intents | natural recommendation questions about the product with the need, each starting differently |

Adapt grammar so it sounds natural (articles, singular/plural: "a sparkling water" → "sparkling water"), but keep product and need recognizable. A need that already starts with "for" does not get a second "for". Give a bare noun the article a person would use ("for a closet", "for the table", "for the car", "for a wedding"), and turn an adjective used as a need into a natural phrase ("bridal" → "for a bride" or "a bridal jewelry set", "outdoor" → "for outdoors"). A format that is a noun ("refills", "tablets", "pods") goes right after the product ("filter refills", "cleaner tablets"), not "in refills". Read each candidate as a shopper would type it: no telegraphic phrasing. Put the structure letter (A = first of the family, B = second…) on each candidate.

OUTPUT: only a JSON array, one object per brief, same order:
[{"id": "...", "candidates": [{"s": "A", "text": "..."}, {"s": "B", "text": "..."}, {"s": "C", "text": "..."}, ...]}]
