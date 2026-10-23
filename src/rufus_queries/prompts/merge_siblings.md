You review sibling leaves of the Amazon US browse tree inside the category: {category}.
Amazon sometimes splits ONE product, as shoppers see it, into two leaves (for example "Seltzer water" and "Carbonated water"). We only want to merge leaves when that is very clear.

Merge two or more leaves ONLY if ALL of these hold:
1. A shopper who searches the most common keywords of one leaf would be just as happy with products from the other.
2. Their top keywords are the same searches written with different words (seltzer / sparkling, k cups / coffee pods), not different products, formats or uses.
3. You are sure. If in doubt, do NOT merge. Most sibling leaves must stay separate.

Never merge leaves that differ in product type (coffee beans vs ground coffee), form (powder vs ready to drink), audience, or use.

INPUT: the sibling leaves under one parent, each with its top keywords and volumes.

OUTPUT: one line per merged group, codes separated by "+", then "|" and a short reason. If nothing should be merged, output exactly: NONE
Example:
L24+L26|both are sparkling water; top searches are the same (seltzer water 24 pack / sparkling water 24 pack)
