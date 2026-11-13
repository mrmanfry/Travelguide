# Impaginazione del libro

Qui vive la grafica del PDF, separata dal codice che lo compone: si cambia il
foglio di stile e cambia il libro, senza toccare Python.

* `libro.css` — tutto: formato, margini, caratteri, colori, box, testatine.
* `fonts/` — EB Garamond e Archivo (licenza SIL OFL, incorporabili nel PDF).

## Le scelte, e perché

**Formato A5 (148×210).** È esattamente metà di un A4: chi stampa in casa ne
mette due per foglio senza tagliare niente. Su un tablet riempie lo schermo. E
la giustezza che ne esce sta sui sessantasei caratteri, la misura in cui
l'occhio non si perde tornando a capo. Un A4 di prosa non è un libro, è una
relazione.

**Margini speculari.** L'interno (17mm) è più largo dell'esterno (14mm), perché
è lì che si perde carta se il libro viene rilegato o pinzato. A schermo non si
nota; stampato sì.

**EB Garamond 10.5/15.2, giustificato, con sillabazione italiana.** Il
giustificato senza sillabazione produce i "fiumi" bianchi tipici dei documenti
mal fatti; con la sillabazione diventa una pagina di libro.

**Un solo accento, il terracotta.** Sei box con sei colori diversi farebbero
volantino. L'identità del box la porta l'etichetta in Archivo maiuscoletto, non
il colore. Due sole eccezioni, che hanno una ragione:

* `ATTENZIONE` — etichetta rovesciata, bianca su terracotta: è l'unico box che
  dice «fermati», e si deve vedere sfogliando.
* `GLI IMMOBILI` — non è un box tematico, è il registro di ciò che le date
  decidono al posto del lettore. Perciò niente filetto laterale: cornice intera,
  come una tabella, in fondo al capitolo.

**Testatine correnti.** A sinistra il titolo del libro, a destra il capitolo che
si sta leggendo: serve a sapere dove si è senza tornare all'indice.

**Capolettera di tre righe** in apertura di capitolo, e indice con i numeri di
pagina veri (calcolati in impaginazione, non stimati).

## Perché WeasyPrint e non il browser

Un PDF ottenuto stampando una pagina web non ha numeri di pagina, non ha
testatine che cambiano col capitolo, spezza i box a metà fra due pagine e lascia
righe orfane in fondo. WeasyPrint implementa davvero le CSS delle pagine
(`@page`, `string-set`, `break-inside`, `orphans`/`widows`, `bookmark-level`),
che è tutto ciò che distingue un libro da una stampata.
