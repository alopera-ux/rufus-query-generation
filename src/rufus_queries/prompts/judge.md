You judge candidate questions that will be sent to Amazon's shopping assistant (Alexa for Shopping, formerly Rufus) to measure which brands it recommends.

INPUT: a JSON list of items. Each has an id, the intent, the product, the need and several options (up to 6; their order is random; ignore order). The options are the same question phrased in different ways: score each one on its own.

For each item, FIRST reason in one sentence, THEN score each option 1-5 on:
- realism: would a real US shopper type this?
- single_need: the product plus the given need only, nothing added
- product_seeking: makes the assistant recommend products
- neutrality: no brand, no bias toward one brand (a device in a compatibility question is fine)
- naturalness: fluent, short, casual

OUTPUT: only a JSON array, one object per item:
[{"id": "...", "reason": "...", "scores": {"1": {"realism": n, "single_need": n, "product_seeking": n, "neutrality": n, "naturalness": n}, "2": {...}, ...}}]
