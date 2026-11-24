# Novità di Scriba

## [0.1.14] - 2026-11-24

### Aggiunte
- Da riga di comando puoi elencare e aprire le riunioni, cercare nei testi e dare un nome alle voci. L’uscita JSON è disponibile per gli altri programmi.
- Scriba salva il massimo di persone viste in Teams, te compreso, e lo propone in Trascrivi.
- La registrazione conserva i conteggi delle letture Teams per minuto. La riga Nomi da Teams avvisa dopo cinque minuti senza bordi di chi parla riconosciuti.
- Un nome letto in un tratto della call può essere proposto per tutta la voce e per voci simili sentite altrove. Nei risultati si vede da dove viene ogni proposta.

### Modifiche
- Scegli il taglio su una sola barra allo stop. Trascrivi mostra la parte tenuta e permette di modificarla partendo dall’originale completo.
- Il salvataggio dei nomi aggiorna i file della trascrizione. Il bilanciamento aggiorna la stessa copia da ascoltare.
- L’anteprima del bilanciamento dura fino a 25 secondi e preferisce un tratto in cui si sentono bene sia la tua voce sia le altre.
- Registrazione e trascrizioni stanno nella stessa cartella. Scriba aggiunge il titolo se lo conosce allo stop e aggiorna la cartella quando lo cambi.
- Gli avvisi audio durante la registrazione sono brevi e in alto. I dettagli sulla rimozione dell’eco compaiono nei risultati.
- Alla fine della call, la finestra mostra il tempo che manca allo stop. La notifica di Windows offre Ferma e Continua anche dall’area di notifica.

### Correzioni
- Il parlato veloce viene segnalato meno spesso come dubbio per la sola quantità di testo.
- Le battute del microfono mantengono il loro posto anche prima e dopo una lunga pausa nella registrazione.
- Se usi ancora «Io», il nome che dai alla voce del microfono viene salvato nelle Preferenze. Puoi annullare dal messaggio di conferma.
- I tratti in cui eri muto nella call diventano silenzio nella copia da ascoltare e nella trascrizione. La rimozione dell’eco e la sua percentuale considerano solo il tempo in cui il microfono era aperto. La traccia originale conserva il suono.
- Scriba legge i nomi di chi parla in Teams sugli schermi ad alta risoluzione, dove il bordo può essere largo un solo pixel.

## [0.1.13] - 2026-11-23

### Aggiunte
- Quando una call finisce (una riunione di Teams, o un programma di chiamata che rilascia il microfono), Scriba propone di fermare la registrazione e, senza risposta, si ferma da solo dopo cinque minuti. L'attesa si cambia nelle Preferenze; con 0 lo stop automatico è spento. Una riunione in presenza non si ferma mai da sola.
- Dopo lo stop scegli **Solo la call** o **Tutta la registrazione** prima della rimozione dell'eco e del mix. Senza risposta, dopo due minuti Scriba tiene la call; la registrazione completa resta nelle revisioni precedenti.
- Puoi tagliare l'inizio e la fine di una registrazione senza ricodificarla; l'originale resta nelle revisioni precedenti.
- Le registrazioni interrotte da un crash, da un'interruzione di corrente o da un'uscita forzata vengono proposte da completare alla riapertura di Scriba, dal passo in cui si erano fermate.
- **Salta la rifinitura** fa uscire prima senza perdere nulla; la copia da ascoltare si crea dopo dalla scheda Trascrivi.

### Modifiche
- Dopo lo stop una sola barra mostra il tempo che manca e l'ora di fine, anche nell'area di notifica e nel dialogo di uscita.
- Il salvataggio della registrazione è più veloce: l'eco si cerca e si toglie solo dove il microfono e le uscite sono attivi.

### Correzioni
- Dopo una call, un microfono aperto dal programma di chiamata (per esempio quello della webcam) non prende più il posto di quello che stava registrando.

## [0.1.12] - 2026-11-22

La 0.1.11 non è mai uscita: le sue costruzioni si sono fermate per una traduzione mancante. La 0.1.12 contiene le stesse modifiche.

### Correzioni
- Quando un'uscita audio passa in un'altra (su Linux un programma di effetti come EasyEffects che manda il suono alle casse; altrove un loopback o un cavo virtuale), Scriba registrava le stesse voci su due tracce: ogni frase veniva trascritta due volte e le voci divise in troppi parlanti. Ora Scriba riconosce le tracce con lo stesso suono, ne trascrive una, lascia la copia fuori dall'audio da ascoltare e lo dice. Il numero di parlanti che scegli torna a valere. Le registrazioni già fatte si correggono trascrivendole di nuovo.
- Su Linux la registrazione non costringe più il resto dell'audio del sistema a blocchi brevissimi: gli effetti che non tenevano il passo gracchiavano mentre si spostavano le finestre.

## [0.1.10] - 2026-11-20

### Modifiche
- Quando trova un aggiornamento, Scriba mostra un avviso in basso con «Aggiorna…» e una notifica di Windows. Puoi aprire la finestra di aggiornamento da uno dei due avvisi o cercare gli aggiornamenti dal menu.

### Correzioni
- Le finestre di dialogo su Windows mostrano una sola linea sopra i pulsanti.

## [0.1.8] - 2026-11-19

### Aggiunte
- Durante la creazione della copia da ascoltare, Scriba mostra la traccia in lavorazione e quanto audio ha elaborato.
- Con il rilevamento di Teams attivo, Scriba può avvisarti quando una riunione rilevata sembra terminata e assegnare i nomi alle voci quando la corrispondenza è univoca.
- Scriba propone un titolo modificabile da Teams o dalla trascrizione. Le nuove esportazioni usano data e titolo.

### Modifiche
- Il cursore cambia il bilanciamento mentre ascolti l'anteprima. Il pulsante mantiene la stessa larghezza tra ascolto e arresto.

### Correzioni
- Le tracce che durano molto più della registrazione vengono limitate nella copia finale. Gli originali sono conservati.
- Scriba raggruppa gli avvisi sulle tracce silenziate. Apostrofi, e commerciali e parentesi angolari vengono mostrati correttamente.
- Se manca il tuo nome, puoi inserirlo dalla pagina Risultato quando scegli «La mia voce».
- L'installer Windows mostra l'icona di Scriba.

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
