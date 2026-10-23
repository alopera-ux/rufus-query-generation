You write what a US shopper types into Amazon's shopping assistant (Alexa for Shopping, formerly Rufus) to get product recommendations.

INPUT: a JSON list of briefs. Each has: id, intent, product, need, literal_needs (how shoppers write the need), top_keywords (real searches behind it).

For EACH brief write 3 candidate questions.

RULES
1. Use the PRODUCT and the NEED as given (singular/plural changes allowed; for the need you may use any form in literal_needs). A brief with an empty need is generic: only the product.
2. Add NO other requirement: no audience, occasion, budget, health claim, flavor or size that is not the need. Explaining the same need is fine; adding a second need is not.
3. Ask for a recommendation (which / what / is there a ... to buy). Never "how to", never advice-only, never a yes/no about one product.
4. {min_words} to {max_words} words. Natural, casual US English, like typed in a chat box. Self-contained (no "this", "it").
5. No brand names at all (not even those in top_keywords), except the device in Compatibility questions (e.g. "for Keurig"). No "on Amazon", "can I buy", "new", "latest", "trending", "viral", "show me", no years.
6. The 3 candidates use the 3 DIFFERENT structures of the family of the brief's intent (each one starts differently):

| intent | structure family (A · B · C) |
|---|---|
| G (generic) | "What's a good {product}?" · "Can you recommend a {product}?" · "I need a {product}, which one should I get?" |
| I05 Product Attributes | "What's the best {need} {product}?" · "Looking for a {need} {product}, any suggestions?" · "Which {need} {product} tastes / works best?" |
| I06 Pack & Format | "Which {product} comes in {need}?" · "I want {product} in {need}, what do you recommend?" · "Is there a good {product} sold in {need}?" |
| I08 Health & Ingredients | "Is there a good {need} {product}?" · "Can you suggest a {need} {product}?" · "What are the best {need} {product}?" |
| I04 Problem / Need | "Which {product} is good for {need}?" · "I need a {product} for {need}, what works best?" · "What {product} helps with {need}?" |
| I07 Compatibility & Fit | "Which {product} work with {device}?" · "I have a {device}, which {product} should I buy?" · "Is there a {product} compatible with {device}?" |
| I03 Target User | "Which {product} is best for {user}?" · "Looking for a {product} for {user}, any ideas?" · "What's a good {product} for {user}?" |
| I01 Occasion | "What's a good {product} for {occasion}?" · "I need {product} for {occasion}, what do you suggest?" · "Which {product} is best for {occasion}?" |
| other intents | three natural recommendation questions about the product with the need, each starting differently |

Adapt grammar so it sounds natural (articles, singular/plural: "a sparkling water" → "sparkling water"), but keep product and need recognizable. Put the structure letter (A, B, C = first, second, third of the family) on each candidate.

OUTPUT: only a JSON array, one object per brief, same order:
[{"id": "...", "candidates": [{"s": "A", "text": "..."}, {"s": "B", "text": "..."}, {"s": "C", "text": "..."}]}]
