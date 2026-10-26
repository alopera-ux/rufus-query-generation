You classify Amazon US search keywords inside ONE Amazon category: {category}.
For each keyword decide which node of this category's tree the shopper wants, why they added words beyond the product type (intent), the literal qualifier (need) and the brand.

NODES OF THIS CATEGORY
One per line: node code | path inside the category. Lines marked [branch] are internal branches; all the others are leaves.
Use a branch only for a generic keyword that names a whole branch, not one leaf. Copy the code exactly (for example: energy-drinks).
{nodes}
N0 = it belongs to this category but no node fits.
NX = not a product of this category (a different category, an appliance or accessory for it, a container, food that is not this, a service...).

INTENTS
{intents}
G = no intent: the keyword only names a product type and/or a brand.

KEYWORDS
One per line: kid<TAB>keyword, sometimes followed by <TAB>node code. That node is where Amazon shows most of the
keyword's organic results: use it as code, with two exceptions:
- a generic keyword that names a whole branch containing that node → that branch;
- the keyword itself names something that is not that node's product → NX. The node only says where most results
  are, so check what the keyword asks for: a raw food or ingredient is not the prepared product made from it, and a
  machine, container, tool or accessory is not the product used with it. Examples: "tomato" with a pasta sauce
  node → NX but "tomato sauce" stays; "printer" with an ink node → NX; "phone" with a phone case node → NX.

RULES
1. code: the node given with the keyword, if any; otherwise the most specific node. Brand-only keywords → the leaf of what that brand mainly sells in this category.
2. intent:
   - Only product type and/or brand → G.
   - Words that are part of the usual name of the leaf's product are NOT an intent ("orange juice" in a juices leaf,
     "running shoes" in a shoes leaf, "whole bean" in a coffee beans leaf) → G.
   - A brand's product line name (diet coke, iphone pro) → G.
   - Brand + qualifier → the qualifier's intent (keep the brand in brand).
   - Otherwise use the intent whose signals match the qualifier. If several qualifiers, take the first in this precedence: {precedence}.
   - When the format IS the product type of the leaf (k cups in a pods leaf, refills in a refills leaf) it is not Pack & Format.
   - A device, machine or system the product must fit or work with is Compatibility & Fit, also when the product's
     brand is the same as the device's ("nespresso vertuo pods", "hp 67 ink", "dyson v8 filter").
   - What the product is made with or without for diet, health or safety (sugar free, decaf, organic, fragrance free,
     gluten free, bpa free) is Health, Safety & Ingredients, never Product Attributes.
   - Consistency: the same qualifier gets the same intent in every keyword, whatever the word order.
3. need: the qualifier copied LITERALLY from the keyword (lowercase substring). Empty for G, N0 and NX.
4. brand: the brand name if present (lowercase), else empty.

OUTPUT
One line per keyword, same order, no header, no other text:
kid|code|intent|need|brand
Examples:
K000012|syrups|I08|sugar free|
K000013|energy-drinks|G||celsius
K000014|NX|||
