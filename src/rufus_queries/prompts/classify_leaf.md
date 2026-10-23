You classify Amazon US search keywords inside ONE Amazon category: {category}.
For each keyword decide which node of this category's tree the shopper wants, why they added words beyond the product type (intent), the literal qualifier (need) and the brand.

NODES OF THIS CATEGORY
Leaves (L..) and internal branches (P..). Use a P code only for a generic keyword that names a whole branch, not one leaf.
{nodes}
N0 = it belongs to this category but no node fits.
NX = not a product of this category (a different category, an appliance or accessory for it, a container, food that is not this, a service...).

INTENTS
{intents}
G = no intent: the keyword only names a product type and/or a brand.

RULES
1. code: the most specific node. Brand-only keywords → the leaf of what that brand mainly sells in this category.
2. intent:
   - Only product type and/or brand → G.
   - A qualifier that is part of the product's own name is NOT an intent: the type of the product ("orange juice", "chamomile tea", "running shoes", "night cream" in a leaf of juices / teas / shoes / creams) → G.
   - A brand's product line name (diet coke, coke zero, gatorade zero) → G.
   - Brand + qualifier → the qualifier's intent (keep the brand in brand).
   - Otherwise use the intent whose signals match the qualifier. If several qualifiers, take the first in this precedence: {precedence}.
   - When the format IS the product type of the leaf (k cups in a pods leaf, juice boxes, flavor packets) it is not Pack & Format.
3. need: the qualifier copied LITERALLY from the keyword (lowercase substring). Empty for G, N0 and NX.
4. brand: the brand name if present (lowercase), else empty.

OUTPUT
One line per keyword, same order, no header, no other text:
kid|code|intent|need|brand
Examples:
K000012|L07|I08|sugar free|
K000013|L03|G||celsius
K000014|NX|||
