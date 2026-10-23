# Generación de queries para Rufus / Alexa for Shopping — v1.2 (21/10/2026)

Historial: v0 (19/10, primera propuesta) → v1.0 (19/10, versión final tras investigación ampliada) → v1.1 (20/10, resultados de la primera prueba con datos reales, sección 17) → v1.2 (21/10, revisión de la prueba, comparación con el repo antiguo y requisitos de la app, secciones 18–20). Recoge el diseño de la generación, el piloto y el protocolo de medición, con las fuentes. Las decisiones que dependen de Toni / del equipo están marcadas con **[DECIDIR]**.

---

## 0. Resumen

1. **Contenido de los datos, forma de la IA.** Lo que pregunta cada query (producto + necesidad) sale literal de nuestras keywords; la IA solo elige la forma de la frase.
2. **Una query = un tipo de producto + una necesidad**, con las palabras que usa la gente, pidiendo una recomendación.
3. **Nada que cambie el grupo de marcas que compiten** salvo la propia subintent: ni marcas de la categoría, ni personas inventadas, ni condiciones extra, ni palabras de actualidad.
4. **Corta**: 6–18 palabras.
5. **Generación por intent** (5 subintents × 3 candidatas en una llamada), **controles automáticos**, **juez** de otra familia de modelos y **equilibrio** del set de 25.
6. **Piloto en Rufus antes de escalar** (450 lanzamientos) para medir si las queries devuelven productos y si la redacción cambia las marcas.
7. **Protocolo de medición** con cuentas limpias, 3 repeticiones y tasa de aparición (no ranking).

---

## 1. Cómo funciona Rufus (lo que condiciona las queries)

- **Nombre**: desde el 13/05/2026 se llama **Alexa for Shopping** (misma tecnología). Está en la app, la web (también en la barra de búsqueda) y Echo Show; solo EE. UU. [About Amazon; Flipflow]
- **Planificador de la pregunta**: un modelo pequeño (query planner) clasifica la pregunta y prepara la búsqueda antes de que el LLM escriba nada. [AWS]
- **Recuperación (RAG)**: catálogo, reseñas, preguntas de clientes y APIs de la tienda; el peso de cada fuente depende de la pregunta. [Amazon Science]
- **COSMO**: grafo de conocimiento construido a partir de búsquedas→compras reales que enlaza productos con funciones, audiencias, ocasiones, lugares, partes del cuerpo, compatibilidades… 15 tipos de relación. Sirve para entender necesidades implícitas ("shoes for pregnant women" → antideslizantes). [Amazon Science; paper COSMO]
- **Selección**: de unos 50 candidatos muestra unos 5; filtra ≥4★ y disponibilidad; influyen atributos, reseñas, ventas. (Análisis de terceros, no confirmado por Amazon.) [PPC Land; Perpetua; Amalytix]
- **Según la pregunta**: amplia → criterios de compra o resultados de búsqueda; concreta → fichas de producto; caso de uso → puede pedir aclaraciones. Filtra bien por presupuesto si se indica. Evita consejo médico. [Amalytix 15 insights]
- **Personalización**: memoria de cuenta (anunciada nov-2025; activa desde abril-2026 según Nova Data): historial, navegación y preferencias reordenan o filtran. Misma pregunta → productos distintos según la cuenta. [About Amazon; Nova Data; Perpetua]
- **Variabilidad**: en 5 repeticiones de la misma pregunta, 2–3 productos aparecían siempre y 4–6 cambiaban. [Amalytix, estudio 1.300+ productos]
- **Anuncios**: Sponsored Prompts facturables desde 25/03/2026, marcados "sponsored"; los "Customers ask" del buscador son orgánicos y salen de las preguntas más clicadas. [Perpetua; Intentwise; Amalytix guía 2026]
- **Otros**: algunas guías comparan opciones también fuera de Amazon; compra automática y "Buy for Me". [Flipflow]
- **Ejemplos oficiales**: "What are the best wireless outdoor speakers?", "Should I get trail shoes or running shoes?", "Comfortable baseball gloves for a 9 year old beginner", "What do I need for a summer party?". [About Amazon; IEEE Spectrum]

### Lo que dice la investigación general (asistentes IA)
- Las listas de marcas casi nunca se repiten igual (<1 de cada 100 ejecuciones), pero **la presencia** de las marcas fuertes es estable → medir **tasa de aparición**, no posición. [SparkToro vía SEJ]
- Añadir **una sola condición** ("for a small team") cambió el 62% de las marcas; repetir la misma pregunta, ~10% (estudio de proveedor). [RanketAI/Clovion]
- Detalles de **persona** en la pregunta cambian las marcas recomendadas (Jaccard entre personas 0,22–0,35 vs 0,42–0,51 dentro de la misma). [arXiv 2605.30207]
- Las queries generadas por LLM tienden a ser **más largas y específicas** que las reales (~8 términos vs 3,8 en plantillas) y menos probables según el uso real. [arXiv 2406.06729]
- Los datos sintéticos son **menos diversos**; un **límite de longitud** fue lo que más aumentó la diversidad; personas detalladas no ganan a las simples. [arXiv 2505.17390]
- Los jueces LLM **se prefieren a sí mismos**, sobre todo cuando se equivocan; razonar antes de decidir lo reduce. Los jueces por pares tienen **sesgo de posición**. [arXiv 2504.03846; arXiv 2406.07791]

---

## 2. Lo que dicen nuestros datos (178.469 keywords)

| Señal | % de keywords | Ejemplo |
|---|---|---|
| Longitud media | 3,06 palabras | "body lotion dry skin" |
| Formuladas como pregunta | 0,04% | — son búsquedas, no preguntas |
| Precio (cheap, budget, under $X…) | 0,10% (solo 0,02% con "under $X") | "cheap keyboard" |
| Comparación (vs, alternative, dupe) | 0,01% | "oura ring dupe" |
| Regalo | 0,60% | "gifts for 5 year old girls" |
| "for" + audiencia (men, kids, dogs…) | 3,3% | "long wallet for men" |
| "best" | 0,07% | "best neck pillow for airplane travel" |

Consecuencias: (1) las keywords dan el **contenido**, no la **forma** de la pregunta; (2) **Price y Comparison casi no existen en keywords**: con ranking por volumen nunca entrarán en el top 5; (3) los umbrales de precio **no pueden salir de las keywords**.

---

## 3. Principios de diseño

- **P1 · Contenido fijo, forma variable.** Producto y necesidad salen literales de los datos; las candidatas solo cambian el marco de la frase. Así las variaciones no cambian qué se pregunta (evita el efecto "una condición extra cambia el 62% de las marcas").
- **P2 · Un producto + una necesidad.** El producto evita que Rufus elija otro tipo de producto; la necesidad es lo que COSMO empareja con atributos.
- **P3 · Pedir recomendación.** Marcos que piden productos ("What's a good…", "Can you recommend…", "Which… should I get"), nunca "how to…".
- **P4 · Neutral.** Sin marcas de la categoría, sin persona salvo que la subintent sea el usuario, sin "newest / 2026 / trending now", sin "on Amazon / can I buy".
- **P5 · Corta y real.** 6–18 palabras, inglés de EE. UU., autosuficiente (sin "this/it").
- **P6 · Calibrada con Rufus.** Piloto antes de escalar y ajustes por intent según lo medido.

---

## 4. Composición de las 25 queries por categoría **[DECIDIR]**

Base acordada: 5 intents × 5 subintents, elegidas por volumen (peso de la hoja × reparto dentro de la hoja). Problemas detectados: no hay preguntas genéricas (la mayor parte del volumen) y Price/Comparison no aparecerán.

| Opción | Filas (5 × 5) | Pros | Contras |
|---|---|---|---|
| A · Pura por datos | 5 intents top | Coherente, ya acordada | Sin genéricas; sin precio ni comparación |
| **B · Genérica + 4 (recomendada)** | 1 fila **genérica** (top 5 tipos de producto del 80%) + 4 intents top | Cubre la demanda principal ("What's a good face moisturizer?"), todo sigue saliendo de datos | Una intent menos |
| C · Genérica + 3 + nativa Rufus | genérica + 3 intents top + 1 fila Price/Comparison | Mide precio y comparación, muy usados en Rufus | Única fila no respaldada por keywords; umbrales de precio de otra fuente |

Fila genérica: subintent = tipo de producto; producto = keyword genérica con más volumen de cada hoja; marco "Can you recommend a good {product}?" / "What's a good {product}?".

---

## 5. La ficha de cada query (determinista, sin IA)

| Campo | De dónde sale |
|---|---|
| category | Sector › Category de Amazon |
| intent + definición | catálogo de 15 intents (Excel) |
| relación COSMO orientativa | tabla de la sección 6 |
| **product_term** | keyword genérica con más volumen clasificada en la hoja donde más pesa la subintent ("body lotion"); si no hay, nombre de la hoja en singular |
| **need_term** | frase de necesidad que devuelve el paso de agrupación; **debe aparecer literal en alguna keyword del grupo** (se comprueba) |
| allowed_modifiers | palabras presentes en las keywords del grupo (p. ej. "cracked", "hands") |
| top_keywords | 3–5 keywords del grupo con su volumen |
| banned_brands | marcas detectadas en las keywords de la categoría (campo marca de la clasificación) + marcas conocidas de la hoja |
| examples | ejemplos oficiales + "Customers ask" de la categoría si se consiguen |

---

## 6. Marcos por intent

Tres marcos para todas: **Q** pregunta ("What's a good {product} for {need}?"), **R** petición ("Can you recommend a {product} for {need}?"), **N** necesidad primero ("I have {need}. Which {product} should I get?"). La tabla adapta el hueco de cada intent.

| Intent | Relación COSMO | Hueco | Ejemplo | Notas |
|---|---|---|---|---|
| I01 Consumption Occasion | used_on, used_for_eve, used_in_loc | for {ocasión/lugar} | What's a good cooler for a beach day? | ocasión literal; sin fechas |
| I02 Gifting | used_by, xInterested_in | gift for {destinatario} | What's a good skincare gift set for my mom? | sin presupuesto salvo keyword |
| I03 Target User & Life Stage | used_for_aud, used_by, xIs_a | for {usuario} | What's a good razor for teens? | el usuario es la subintent; no añadir rasgos |
| I04 Problem / Need | used_for_func, used_in_body, used_to | for {problema} | What's a good lotion for eczema-prone skin? | lenguaje de producto, no médico |
| I05 Product Attributes | atributos de catálogo, capable_of | {atributo} {producto} | Can you recommend an unscented body wash? | |
| I06 Pack & Format | is_a, used_as, atributos de tamaño | {formato} {producto} | Can you recommend a travel-size shampoo? | formato literal de las keywords |
| I07 Compatibility & Fit | used_with | that fits {dispositivo} | What's a good case for an iPhone 16? | **sí** se nombra el dispositivo: no compite en la categoría |
| I08 Health, Safety & Ingredients | atributos (ingredientes, certificaciones), used_in_body | {sin X / con X} | Is there a good fragrance-free baby wipe? | |
| I09 Sustainability & Ethics | atributos (certificaciones) | {atributo eco} | What's a good plastic-free dish soap? | |
| I10 Price Sensitivity | filtro de precio | under ${X} | What are good headphones under $100? | X de datos de precio (sección 11) |
| I11 Quality & Durability | reseñas, capable_of | most durable / long-lasting | Which phone case is the most durable? | Rufus lo responde con reseñas |
| I12 Social Proof & Trends | ventas, web | best-selling / top-rated | What's a top-rated sunscreen? | evitar "trending now", "TikTok viral" (temporal) |
| I13 Brand Intent | — | **excluida** | — | no hay queries con marca |
| I14 Comparison & Alternatives | comparador | {tipo A} or {tipo B} for {necesidad} | Should I get a gel or a cream moisturizer for oily skin? | tipo vs tipo de hojas hermanas del 80%; nunca marcas |
| I15 Discovery & Availability | — | — | — | entrega y suscripción **fuera** (dependen de ubicación/cuenta); "discovery" se cubre con la fila genérica |

---

## 7. Generación

- **Unidad de llamada**: una por intent (5 subintents × 3 candidatas = 15). 5 llamadas por categoría → ~1.625 para 325 categorías.
- **Modelo**: el más capaz disponible en Bedrock para escribir; temperatura media (~0,7); partes fijas en caché; salida JSON.
- **Esqueleto del prompt** (inglés):

```
SYSTEM
You write what a US shopper types into Amazon's shopping assistant (Alexa for Shopping, formerly Rufus) to get product recommendations.
Rules:
- Use the PRODUCT and NEED exactly as given (plural/singular changes allowed). Add no other requirement.
- Ask for a recommendation. Never "how to", never advice-only.
- 6 to 18 words, natural US English, self-contained (no "this", "it").
- No brand names except a device in compatibility questions. No "on Amazon", "can I buy", "newest", "trending".
- One candidate per frame: Q (question), R (request), N (need first).
Good: "What's a good body lotion for very dry, cracked skin?"
Bad: "Is CeraVe good for dry skin?" (brand) · "How do I treat dry skin?" (advice) · "What's a cheap unscented lotion for kids with dry skin?" (several needs)

USER
Category: Beauty and personal care › Skin care
Intent: Problem / Need — what problem the product must solve.
Sub-intents:
1. product: "body lotion" · need: "very dry skin" · allowed extra words: cracked, hands, legs · keywords: "lotion for very dry skin" (…), "cream for dry cracked hands" (…)
2. …
Return JSON: [{"id":1,"candidates":[{"frame":"Q","text":"…"},{"frame":"R","text":"…"},{"frame":"N","text":"…"}]}, …]
```

---

## 8. Controles automáticos (sin IA)

Una candidata se descarta si falla alguno:
- no contiene product_term o need_term (con tolerancia de plural);
- contiene una marca de banned_brands;
- contiene frases prohibidas ("on Amazon", "can I buy", "I can", "show me", "newest", "latest", "2026", "trending", "viral");
- fuera de 6–18 palabras;
- usa "this/it/these" sin referente;
- palabras no permitidas que añaden condición (comprobado por el juez si no es detectable por reglas).

---

## 9. Juez

- **Otra familia de modelos** si hay acceso (si escribe Nova, juzga Claude, o al revés); si no, el mismo modelo con razonamiento previo obligatorio.
- **Comparativo**: recibe la ficha y las 3 candidatas que pasaron los controles, **en orden aleatorio**; razona y devuelve mejor y segunda. Si la diferencia es pequeña, se repite con el orden invertido.
- **Criterios** (aprobado/no + nota 1–5): realismo (lo escribiría un comprador), una sola necesidad, pide productos, neutralidad, naturalidad.
- Se guarda la **segunda mejor como variante B**.
- Una llamada por intent (~1.625; ~3.250 con la repetición de orden).

---

## 10. Equilibrio del set de 25

- Marcos repartidos (~8–9 de cada uno).
- "best" como máximo en 3 queries.
- Sin casi duplicados (similitud de palabras < 0,55).
- Máximo 8 queries sobre la misma hoja (aviso, no bloqueo).
- Si algo falla, se cambia a la variante B de la query afectada.

---

## 11. Casos especiales

- **Pocos datos** (31 categorías con una sola hoja; categorías con pocas keywords): se completa con la siguiente subintent de otra intent de la misma categoría; si aun así faltan, la IA propone necesidades marcadas `inferred` para revisión.
- **Salud**: necesidad como característica de producto ("for eczema-prone skin"), nunca tratamiento o curación.
- **Precio** (si se usa): umbral = percentil ~25 de precios de la hoja redondeado a 10/15/20/25/50/100 $ (requiere datos de precio de Shalion); sin datos, "affordable".
- **Comparación** (si se usa): dos hojas hermanas del 80% que sean sustitutas (lo valida la IA).
- **Tendencias**: "top-rated / best-selling", nunca temporal.

---

## 12. Salida (una fila por query)

query_id · category · intent · sub_intent · product_term · need_term · leaf · top_keywords (con volumen) · score de la subintent · frame · query (A) · variante (B) · notas del juez · flags (`inferred`, `borrowed`) · versión del set.

---

## 13. Piloto en Rufus (antes de las ~8.125)

- **Diseño**: 3 categorías de tipo distinto (belleza, electrónica, alimentación/hogar) × 25 queries × 2 variantes (A, B) × 3 repeticiones = **450 lanzamientos**.
- **Métricas y objetivos**:

| Métrica | Definición | Objetivo |
|---|---|---|
| Tasa de productos | respuestas con ≥1 ficha de producto | ≥ 90% |
| Tasa de aclaración | respuestas que piden más información | ≤ 5% |
| Estabilidad | Jaccard medio de marcas entre las 3 repeticiones | referencia de ruido |
| Sensibilidad a la redacción | Jaccard de marcas entre A y B | no peor que la estabilidad |
| Patrocinados | % de productos "sponsored" | registrar |

- **Ciclo**: por intent y por marco, ajustar lo que falle y repetir solo esa parte.

---

## 14. Protocolo de medición (equipo que lanza)

- **Cuentas** limpias e iguales (sin historial ni preferencias), **misma ubicación** (ZIP) y mismo canal (app o web).
- **Una conversación nueva por query**, una sola vuelta: si Rufus pide aclaración, se registra y no se responde.
- **3 repeticiones** por query y oleada (5 en el piloto).
- **Guardar**: texto completo, productos (ASIN, marca, posición, sponsored sí/no), si pidió aclaración, retailers externos, fecha y hora.
- **Métrica**: tasa de aparición de cada marca por query (apariciones / repeticiones) y media por categoría; intervalos de Wilson y conteos; el ranking solo como dato secundario.
- **Precisión orientativa** (25 × 3 = 75 respuestas por categoría): ±7–10 puntos para una marca con un 10% de visibilidad y ±10–16 con un 30–50% (el rango alto supone una correlación de 0,5 entre repeticiones de la misma query).
- **Mismo set entre oleadas**; versionar cualquier cambio.

---

## 15. Limitaciones y riesgos

- Medimos **una sola vuelta**; en Rufus la gente sigue preguntando y eso cambia las marcas.
- Las cuentas limpias miden un **comprador base**, no la experiencia personalizada.
- Alexa for Shopping cambia rápido (modelos, anuncios, opciones fuera de Amazon): repetir el piloto si hay cambios grandes.
- Las keywords representan búsquedas, no preguntas a Rufus; precio y comparación están infrarrepresentados.
- Los factores de selección de productos vienen de análisis de terceros.

---

## 16. Pendiente

- **[DECIDIR]** composición de las 25 (A, B recomendada, C).
- **[DECIDIR]** si se usa Price: ¿hay datos de precio por hoja en Shalion?
- Modelos disponibles en Bedrock para escribir y juzgar (depende del acceso AWS).
- Acordar el protocolo y el piloto con el equipo que lanza; pedirles los "Customers ask" de las categorías del piloto.
- Search volume por keyword (imprescindible para ordenar hojas, intents y subintents). **Prueba con datos reales: 22/10.**
- Probar el paso 178k → 325 categorías y medir estabilidad del foco/intents (sección 18).
- App con inputs configurables (sección 20).

---

## 17. Primera prueba con datos reales (20/10/2026) — Beverages

**Montaje**: categoría Grocery and gourmet food › Beverages (59 hojas), con las 2.538 keywords que usó el repo antiguo. Clasificación con IA en 15 lotes; foco, intents y subintents con las fórmulas acordadas; 3 candidatas por subintent, controles automáticos y juez (otra instancia, orden aleatorio). Resultados completos en `prueba_beverages_resultados.xlsx`; test a ciegas en `test_a_ciegas_beverages.xlsx`.

**Resultados**

| | Repo antiguo (465) | Nuevo, opción B (25) |
|---|---|---|
| Con marca | 33% | 0% |
| Frases prohibidas ("on Amazon", "I can…") | 56% | 0% |
| Pregunta o petición | 34% | 100% |
| Estilo keyword | 21% | 0% |
| Palabras (media) | 12,1 | 6,8 |
| Comienzos distintos | 76% | 16% |

- El 78,8% de la demanda son keywords genéricas o solo de marca. **Las 25 de la opción B representan el 69% de la demanda; la opción A, el 13%.** *(Ojo: todos los % de esta prueba usan peso = 1/ranking, no search volume real; ver sección 18.)*
- Foco 80%: 7 hojas. Intents top: Pack & Format 36,6%, Health & Ingredients 31,7%, Product Attributes 27,1%, Problem/Need 2,8%, Target User 1,7%.
- Target User solo tiene 2 necesidades distintas: la opción A obliga a tomar prestadas 3 queries.
- Controles: 79 de 90 candidatas pasan; el marco "necesidad primero" falla un 33% porque añade contexto. El juez nunca lo eligió. Nota media del set B: 4,86/5.

**Hallazgos que cambian el diseño**

1. **El "KEYWORD_VOLUME" del repo antiguo es un ranking** (1 = más buscado). El repo antiguo lo usaba al revés. Si nuestro dato es un ranking: peso = 1/ranking (o pedir volumen real).
2. **Coherencia entre lotes**: la misma marca cayó en hojas distintas según el lote ("liquid iv"). Añadir **anclas por marca** (la hoja de su keyword con más peso) y aplicarlas en todos los lotes.
3. **Líneas de producto con "diet/zero"** (diet coke, coke zero) = G, no Health. "zero sugar soda" genérico = Health.
4. **Necesidades ligadas a una sola marca** ("milkshake" solo de Nurri) se descartan: una subintent necesita ≥2 marcas o keywords sin marca.
5. **Hojas con pocas keywords** también usan la mezcla media en el paso de subintents (no solo en intents).
6. **need_term = calificador literal** ("100%"), nunca una descripción ("100% juice").
7. **product_term** = la keyword principal del grupo sin la necesidad ni la marca; si queda algo genérico ("water", "shake"), el término principal de la hoja. La necesidad no puede estar contenida en el producto ("energy drink for energy").
8. **Formatos que son el producto** ("water flavor packets", "juice boxes") se clasifican como producto, no como Pack & Format.
9. **Longitud mínima 4 palabras**: "What's a good cherry soda?" es natural.
10. **Quitar el marco "necesidad primero"**: añade contexto. Usar dos marcos neutros (pregunta y petición) alternados de forma fija. A cambio hay poca variedad de comienzos: mejor para comparar mediciones, peor en realismo; validarlo en el piloto.

**Pendiente de esta prueba**: puntuación humana a ciegas, prueba en Rufus, y repetir con un modelo distinto como juez (aquí escritor y juez son el mismo tipo de modelo).

---

## 18. Revisión de la prueba de Beverages (21/10/2026)

**El peso no es search volume.** La columna `KEYWORD_VOLUME` del repo antiguo es un ranking de frecuencia de búsqueda ("liquid iv" = 159, "orange juice" = 472, las raras ≈ 1.000.000). En la prueba se usó peso = 1/ranking, normalizado por categoría. Es una suposición (caída tipo Zipf), no un dato. El **orden** de hojas es fiable; los **% exactos no**, y el corte del 80% depende de ellos. El fichero de 178k solo trae el texto de la keyword (sin volumen ni ranking). **Regla: no se usan pesos supuestos en el pipeline real; mañana (22/10) se repite con search volume real.**

**Long tail de Beverages** (fuera del foco 80%): 24 hojas con keywords (≈15% del peso) y 28 sin ninguna.
- Tiene sentido: Bloody mary, Squash, Ciders, Cocoa, Chocolate drink mixes, Lemonade, Sparkling juice, Smoothies, Meal replacement, Syrups (SodaStream), Flavored water, Water flavoring drops, Sports drinking water.
- Artefacto de los datos: **Coffee (6 hojas) y Tea (16 hojas)**. El fichero del repo antiguo no tiene ninguna keyword con "k cup", "tea", "ground coffee", "coffee beans" ni "kombucha"; el café solo entra vía "espresso". El fichero de 178k sí los tiene (166 "k cup", 1.140 "tea", 52 "coffee pods").
- Problema del método: **Sports drinks (4,5%) y Coconut water (4,7%)** quedan fuera por poco del corte duro. Probar la regla "80% o cualquier hoja ≥3%". Seltzer water (1%) se separa de Carbonated water por cómo divide Amazon el árbol.
- Ruido: "cola de caballo" (hierba) y "cola cao" entraron por contener "cola".

**Calidad del mapeo keyword → hoja.** Revisión manual de 120 keywords al azar (criterio propio, no medida): la gran mayoría bien; ~5 discutibles ("dill pickle juice", "real lime juice", "fresh lemon juice", "cherry juice concentrate", "liquid iv"); 1 probablemente mal ("liquid death mountain don't" en Still water). **No probado**: el paso 178k → 1 de 325 categorías (solo se probó dentro de Beverages) ni la comparación con breadcrumbs reales.

**Precisión: no se busca el 100%.** Los errores en keywords de poco peso no cambian el foco ni los intents; los de las keywords más buscadas sí. Enfoque:
1. Clasificación automática de todas las keywords.
2. Revisión manual solo de las keywords de más peso de cada categoría (+ marcas ancla).
3. Medir **estabilidad**: si al corregir esas keywords el foco y los 5 intents no cambian, la clasificación es suficiente.

---

## 19. Por qué el pipeline nuevo es mejor que el repo antiguo

La diferencia principal: el repo antiguo pedía al LLM que **inventara sobre qué preguntar**; el nuevo lo **saca de lo que la gente busca**.

| | Repo antiguo (rufus-query-battery) | Pipeline nuevo |
|---|---|---|
| Universo | Una categoría por ejecución, sin árbol | Árbol completo de Amazon (18.564 hojas), igual para todas |
| De dónde salen los temas | Clusters léxicos (TF-IDF/HDBSCAN) + dimensiones que Nova Pro inventa a partir del nombre de la categoría | Cada keyword clasificada en hoja, intent y necesidad |
| Intents | Inventados en cada ejecución → no comparables entre categorías | Los 15 del Excel, fijos y comunes |
| Prioridad | "High/Medium/Low" que decide el LLM; el volumen no cuenta | Peso por demanda: foco 80%, top intents y sub-intents por peso |
| Marcas | El prompt pedía "Use REAL brand names" | Preguntas neutras, sin marcas de la categoría |
| Control de calidad | Solo regex | Controles automáticos + juez LLM |
| Forma de la pregunta | 5 formatos genéricos | Adaptada a Rufus: un producto + una necesidad, pide recomendación, corta |

Medido en Beverages (465 antiguas vs 25 nuevas): marca 33% → 0%; frases plantilla 56% → 0%; pregunta o petición real 34% → 100%; palabras de media 12,1 → 6,8.

Por qué importa: (1) **representatividad**: preguntas que reflejan la demanda → visibilidad medida parecida a la real; (2) **comparabilidad** entre categorías y en el tiempo; (3) **medida limpia**: si la pregunta nombra una marca, Rufus la repite.

Aún no demostrado: variedad (comienzos distintos 76% → 16%; se está arreglando con preguntas distintas por intent); cobertura de demanda (depende del volumen real); que Rufus responda mejor (piloto en Rufus + test a ciegas).

Se reutiliza del repo antiguo la infraestructura (cliente Bedrock, modo mock, UI FastAPI, jobs en paralelo).

---

## 20bis. Regla de trabajo y cambio en el paso 6 (21/10/2026)

**Regla**: razonar y evaluar siempre desde el caso real: **peso = suma del search volume** de las keywords. Nada de conteos de keywords ni pesos supuestos (1/ranking) para sacar conclusiones; si un dato no está, se dice y se espera al dato real.

**Paso 6, nuevo diseño — agrupación dentro de la hoja**:
1. En cada hoja del foco, agrupar las keywords que son **la misma búsqueda con otras palabras** (mismo producto + misma necesidad: orden, plurales, sinónimos, erratas). Si cambia la necesidad, es otro grupo ("sugar free" ≠ "low sugar"). Las keywords con marca suman peso; la pregunta va sin marca.
2. Peso del grupo = **suma del search volume** de sus keywords.
3. Por cada intent elegido: top 5 grupos por peso entre todas las hojas del foco, con tope por hoja (por intent y en el set de 25).
4. La pregunta sale del propio grupo (producto + necesidad), con la estructura de su intent.

**Por qué**: con el método actual (agrupar necesidades de toda la categoría) el peso no corresponde a la pregunta. Ejemplo del test: "Sugar free" juntó 139 keywords (36 energy drinks, 35 soft drinks, 33 juice, 20 drink mixes…) y la pregunta salió solo sobre water flavor packets; no hubo pregunta de sugar free soda.

**Paso 4, ajuste conservador — hojas hermanas que son el mismo producto**: como todo se decide a nivel de categoría, una hoja que Amazon divide en dos (p. ej. Seltzer water / Carbonated water) pierde peso en el foco, parte grupos y duplica cupo. Regla: **por defecto no se junta nada**. Solo se juntan, de forma automática y solo para seleccionar preguntas (el universo y el dashboard siguen con las hojas de Amazon), si se cumplen las dos:
1. mismo padre en el árbol;
2. las búsquedas principales de la hoja pequeña tienen su equivalente en la otra (mismas búsquedas con otras palabras), con un umbral estricto a fijar con datos reales.
En caso de duda, no se juntan.

**A vigilar**: grupos de necesidad demasiado amplios ("Organic" mezclaba "organic lemon juice"); concentración en pocas hojas; Product Attributes muy fragmentado. Prototipo: `pruebas/clusters_in_leaf.py` (cambiar el conteo por la suma de volumen con los datos reales).

---

## 20ter. Prueba completa de Beverages con search volume real (21/10/2026)

**Datos**: `amazon_us_volume.xlsx` = top 200.000 keywords de Amazon US con volumen (11,3M → 5.056; **septiembre 2026**, confirmado por Toni). Contiene 164.903 de las 178k keywords de Shalion.
**El peso 1/ranking era incorrecto**: el volumen real cae ÷3–5 por cada ×10 de ranking, no ÷10. Descartado.

**Proceso** (resultados en `pruebas/prueba_beverages_volumen_real.xlsx`, gráfico `pruebas/beverages_universe_real.png`):
1. Preselección por palabras y marcas de bebida en las 200k → 10.559 candidatas (recall 99,7% sobre keywords de bebida conocidas).
2. Clasificación con IA (11 lotes): **3.141 keywords de Beverages, 90,6M de volumen**; 7.418 no lo son (cafeteras, botellas, protein powder…). Genéricas de rama ("coffee" 1,08M, "water", "tea", "juice") se reparten entre sus hojas por volumen.
3. Normalizaciones deterministas: tipo de fruta en zumos y de hierba en tés = producto (G); líneas de marca sin azúcar (diet coke, red bull sugar free) = G.
4. **Foco 80%: 14 hojas** (81,1%): capsules & pods 12,1%, soft drink mixes 11,4%, energy drinks 11,4%, protein drinks 8,5%, soft drinks 7,2%, syrups 4,9%, herbal tea 4,5%, still water 4,2%, fruit juice 4,2%, carbonated water 2,7%, sports drinks 2,7%, matcha 2,5%, ground coffee 2,4%, instant coffee 2,3%.
5. Grupos dentro de hoja + intent (556 necesidades → 478 grupos). Peso = % hoja × % grupo en su hoja.
6. Ranking de intents: Attributes 29,7%, Pack & Format 26,4%, Health & Ingredients 23,5%, Problem/Need 10,2%, Compatibility 7,5%.
7. Opción B: 5 genéricas + 4 intents × 5, tope 2 por hoja e intent y 4 por hoja. Excluidos grupos ligados a una marca (1 familia de marca y <25% sin marca), p. ej. "witches brew" (Alani), "gatorade powder".
8. 75 candidatas, 75 pasan controles; juez a ciegas, nota media 4,94. Media 6,3 palabras.

**Hallazgos**
- Con datos reales Beverages es otra categoría: café (pods, ground, instant), electrolitos, protein shakes, syrups y herbal tea pesan mucho; con el fichero antiguo no aparecían.
- Hacen falta reglas de **familia de marca** (alani = alani nu; gatorade = gatorlyte) y detectar nombres de producto sin marca ("throat coat").
- El **término de producto automático** falló en 10 de 25 fichas ("instant", "pods", "shake"): regla nueva = si el resto tras quitar la necesidad no es un tipo de producto, usar la genérica principal de la hoja.
- El juez vuelve a preferir "What's a good/best…" (11 comienzos distintos de 25). Opción: dar prioridad a la estructura propia del intent salvo diferencia clara de nota.
- La preselección por palabras no es la clasificación de producción: en producción, clasificar las 200k a una de las 325 categorías (paso 2a).

---

**Decisión 22/10/2026 — G (sin intent) compite como un intent más.** Las keywords genéricas sin marca ("energy drink", "coffee pods") forman el intent G. Su peso entra en el ranking igual que el resto; si queda entre los N primeros (config `selection.n_intents`, 5 por defecto) tiene su fila de 5 preguntas, y si no, no. Sustituye a la fila genérica fija de la opción B.
- Base común para comparar: share de un intent en su hoja = volumen del intent / (volumen con intent + genéricas sin marca). Las keywords de solo marca siguen contando solo para el peso de la hoja (Brand Intent excluido).
- Dentro de G, los "grupos" son tipos de producto de cada hoja (p. ej. "coffee pods" = coffee pods, k cups, kcups).
- Beverages (septiembre 2026, hojas del foco): genéricas sin marca 23,3% del volumen, solo marca 47,4%, con intent 29,4% → G sería el primer intent.
- No se reparten las 25 en proporción al peso: con solo-marca como genéricas G sería ~70% y quitaría casi toda la variedad.

---

## 20. App (requisito de Toni, 21/10/2026)

Una app que ejecute el pipeline completo a partir de inputs del usuario.

**Inputs**
1. **Árbol de breadcrumbs** (fichero; p. ej. el formato de `us_2026-09.xlsx`).
2. **Keywords con su search volume** (CSV: keyword + volumen).
3. **Número de intents** a seleccionar (N).
4. **Número de preguntas por intent** (se puede fijar por intent).

**Qué hace**: pasos 1–9 del pipeline (clasificar → pesar → foco → intents → sub-intents → generar → controles → juez → set final).

**Pendiente de definir**: si el umbral del foco (80%) y la fila genérica (opción B) también son parámetros; si la app permite revisar a mano las keywords de más peso antes de seguir; formato de salida (Excel/CSV con pasos intermedios); si se monta sobre la UI FastAPI del repo antiguo.

---

## Fuentes

- Amazon Science — The technology behind Rufus: https://www.amazon.science/blog/the-technology-behind-amazons-genai-powered-shopping-assistant-rufus
- Amazon Science — Building commonsense knowledge graphs (COSMO): https://www.amazon.science/blog/building-commonsense-knowledge-graphs-to-aid-product-recommendation
- COSMO paper: https://cdn.amazon.science/19/5d/bde30d0d4019be6421e79e50cda9/cosmo-paper.pdf
- AWS — How Rufus doubled its inference speed (query planner): https://aws.amazon.com/blogs/machine-learning/how-rufus-doubled-their-inference-speed-and-handled-prime-day-traffic-with-aws-ai-chips-and-parallel-decoding
- About Amazon — How to use Rufus: https://www.aboutamazon.com/news/retail/how-to-use-amazon-rufus
- About Amazon — Personalized features: https://www.aboutamazon.com/news/retail/amazon-rufus-ai-assistant-personalized-shopping-features
- Flipflow — Alexa for Shopping launch: https://www.flipflow.io/en/?p=28487
- Perpetua — Alexa for Shopping guide: https://perpetua.io/blog-alexa-for-shopping-amazon-rufus-the-complete-guide-for-brands-and-sellers/
- Amalytix — Rufus study (1.300+ products): https://www.amalytix.com/en/knowledge/ai/amazon-rufus-pattern-analysis/
- Amalytix — Alexa for Shopping 2026 guide: https://www.amalytix.com/en/knowledge/ai/amazon-rufus-guide-2026/
- Amalytix — 15 insights: https://www.amalytix.com/en/knowledge/ai/amazon-rufus/
- PPC Land — Rufus shows 5 products: https://ppc.land/rufus-shows-5-products-not-50-what-brands-must-know-about-amazons-ai-filter/
- Nova Data — Rufus account memory: https://novadata.io/resources/news/amazon-rufus-account-memory-personalization-2026
- Intentwise — Sponsored Prompts: https://www.intentwise.com/blog/explained-sponsored-prompts-ads-on-amazon
- IEEE Spectrum — Rufus: https://spectrum.ieee.org/amazon-rufus
- TechCrunch — Rufus review: https://techcrunch.com/2024/03/05/amazons-new-rufus-chatbot-isnt-bad-but-it-isnt-great-either
- Search Engine Journal — SparkToro study: https://www.searchenginejournal.com/ai-recommendations-change-with-nearly-every-query-sparktoro/566242/
- ThriveStack — non-determinism: https://www.thrivestack.ai/research/ai-visibility-tracking-non-determinism
- Answer Engine Land — confidence intervals: https://answerengineland.com/ai-search/ai-search-visibility-confidence-intervals-a-practical-guide-to-variance-and-sample-size/
- RanketAI — follow-up volatility: https://www.ranketai.com/en/blog/deep-dive-ai-recommendation-volatility-2026-07-08
- arXiv 2605.30207 — Persona conditioning of brand recommendations: https://arxiv.org/html/2605.30207v1
- arXiv 2406.06729 — Synthetic queries for virtual assistants: https://arxiv.org/pdf/2406.06729
- arXiv 2505.17390 — Lexical diversity with persona prompting: https://arxiv.org/html/2505.17390v2
- arXiv 2504.03846 — LLM evaluators prefer themselves: https://arxiv.org/html/2504.03846v1
- arXiv 2406.07791 — Position bias in LLM judges: https://arxiv.org/html/2406.07791v6
