# Novità di Scriba

## [0.1.7] - 2026-11-18

### Modifiche
- Scriba propone la finestra dell'aggiornamento una volta per versione, quando il lavoro in corso è finito. Con «Più tardi» l'aggiornamento resta disponibile nella barra.

### Correzioni
- Quando GitHub limita le richieste, Scriba controlla la pagina del rilascio. Prima di avviare l'installer ne verifica l'impronta e la firma.
- Su Windows, all'apertura o al ripristino della finestra, Scriba controlla gli aggiornamenti se sono passati almeno 30 minuti dall'ultimo controllo riuscito. Se è al lavoro, aspetta che il lavoro finisca.
- Il controllo manuale funziona durante la prova della scheda video e l'ascolto. Se deve aspettare, Scriba spiega il motivo e poi mostra l'esito.
- Le riunioni si riaprono anche se una riga della trascrizione ha gli orari invertiti. Scriba corregge la riga, la marca come dubbia e spiega la correzione nella pagina Risultato.

## [0.1.6] - 2026-11-17

### Aggiunte
- Riconoscimento delle voci più preciso con community-1. I quattro modelli vocali si scaricano al primo avvio e dopo l'aggiornamento, per circa 33 MB.
- Il riconoscimento delle voci usa la scheda video tramite Vulkan quando è disponibile e passa automaticamente al processore se necessario. Il metodo precedente resta disponibile.
- Le proposte modificabili distinguono più voci nello stesso riquadro di Teams e propongono un nome comune per voci molto simili. Le riunioni precedenti si riaprono anche senza connessione.
