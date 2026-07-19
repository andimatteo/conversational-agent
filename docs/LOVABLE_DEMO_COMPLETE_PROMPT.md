# QuoteWise — prompt completo per Lovable

Incolla questo prompt integralmente nel progetto Lovable esistente. Modifica il
frontend reale: non creare mock, slideshow, timer che inventano stato, Supabase,
funzioni server duplicate o workflow GitHub.

## Obiettivo

QuoteWise deve avere quattro sole sezioni per ogni job:

1. **Intake**
2. **Spec**
3. **Calls**
4. **Compare**

Rimuovi completamente **Call list** da sidebar, tab, routing primario, CTA e flusso
demo. Se un vecchio link apre `/job/:jobId/call-list`, redirigilo a
`/job/:jobId/calls`. La discovery è un dettaglio backend automatico, non una pagina.

Usa come backend:

```ts
export const API_BASE =
  "https://travesti-championship-presented-machinery.trycloudflare.com";
```

Ogni richiesta protetta usa `Authorization: Bearer <token>`. Mantieni login,
registrazione, logout e isolamento per utente già presenti.

## Verità della demo

- Dopo la review, il backend esegue una chiamata **reale e nuova a Google Places**.
- Tutte le aziende mostrate provengono da quel risultato Google Places: nome,
  Place ID, telefono salvato, coordinate, indirizzo, rating e recensioni sono reali.
- Discovery e telefonia sono due cose separate.
- Nessun numero telefonico Google viene chiamato durante la demo.
- N−1 telefonate sono simulate dal backend come transcript progressivi e quote
  strutturate. Devono essere sempre etichettate come sintetiche.
- Un solo vendor Places viene rappresentato dal role-player umano configurato sul
  server. Il browser non conosce e non invia il suo numero completo.
- Lo stesso umano viene chiamato esattamente due volte: come Explorer nel primo
  quote batch e come Closer nell'ultimo batch automatico.
- La seconda chiamata parte solo dopo che tutti i quote batch sono terminali e la
  knowledge finale è stata pubblicata.
- Le offerte sintetiche possono essere citate soltanto come “simulated demo-market
  offers”; non attribuirle mai alle vere aziende Google visualizzate.
- Il browser non avvia separatamente la callback, non esegue retry automatici e non
  accetta numeri telefonici.

## Navigazione

Desktop: Jobs, Domains, Profile. Dentro un job mostra esclusivamente:

```text
Intake | Spec | Calls | Compare
```

Su mobile usa un drawer compatto. Gli archiviati sono read-only. Mostra sempre il
banner runtime restituito da `GET /api/runtime-config`.

Il runtime include:

```ts
type RuntimeConfig = {
  debug_mode: boolean;
  debug_behavior: string;
  debug_notice: string;
  demo_phone_configured: boolean;
  demo_phone_masked: string;
  twilio_number_configured: boolean;
  live_vendor_calls_enabled: boolean;
  demo_intake_pdf_url: string;
  call_list_ui_enabled: false;
  review_launch_endpoint: "/api/jobs/{job_id}/launch";
  google_places_live_at_launch: true;
  google_places_configured: boolean;
};
```

Non aggiungere un toggle debug nel client.

## Stato end-to-end

Renderizza lo stato reale del backend:

```text
NEW_JOB
 → DOCUMENT_UPLOADING → DOCUMENT_PARSED
 → READY_TO_REVIEW
   ↘ optional VOICE_INTAKE_CONNECTED when the user chooses it
 → REVIEW_AUTHORIZED
 → GOOGLE_PLACES_DISCOVERING
 → GOOGLE_PLACES_COMPLETE
 → QUOTE_BATCH_1 (umano Explorer + peer sintetici)
 → QUOTE_BATCH_2 … QUOTE_BATCH_N
 → AUTO_NEGOTIATION_QUEUED
 → AUTO_NEGOTIATION_CALLING (stesso umano, Closer grounded)
 → COMPLETE → COMPARE
```

Non inserire uno stato Call List e non chiedere al cliente di selezionare aziende.

## 1. Intake — `/job/:jobId/intake`

Mantieni entrambe le modalità richieste dall'hackathon.

### Documento

- Dropzone multipart verso `POST /api/jobs/:jobId/documents`, campo `file`.
- Accetta PDF, immagini e testo secondo gli errori backend.
- Dopo upload mostra filename, campi estratti, correzioni, insight, eventuali quote
  documentali e validation errors.
- Refetch Job, Documents e Intake Form dopo ogni upload.

### Voice intake opzionale

- `POST /api/jobs/:jobId/voice-session` restituisce la signed URL ElevenLabs.
- Usa il client React ufficiale ElevenLabs per una conversazione da computer.
- Passa esclusivamente gli identificatori restituiti dal backend.
- L'Estimator legge i dati del documento e chiede solo le informazioni mancanti.
- Mostra Connected, Listening, Speaking, Disconnected e gli errori reali.
- Dopo disconnect refetch Job e `GET /api/intake-form`.
- Non simulare transcript o completamento dell'intervista nel browser.
- Non rendere mai l'intervista un prerequisito della review o di `/launch`.
- Non mostrare “Complete the short voice interview before review and launch”. Una
  specifica completa e valida ottenuta da documento + form può partire direttamente.

### Form condiviso

Genera il form dallo schema di `GET /api/intake-form?vertical=...&area_code=...`.
`PUT /api/jobs/:jobId/spec {spec}` salva nello stesso oggetto popolato da documento e
voce. Qualunque modifica azzera la conferma. Mostra campi mancanti e learned questions.

CTA finale: **Review complete job spec** → Spec.

## 2. Spec e lancio — `/job/:jobId`

Mostra la specifica completa in modo generico: sezioni per object, tabelle per list,
label/value per scalar. Mostra provenienza documento/voce/form, missing fields e
learned questions.

Per un prepared demo non usare `POST /confirm`. La review e il lancio sono un'unica
azione deliberata e atomica.

Mostra una card **Review, authorize and launch** con:

- specifica confermata visivamente;
- testo: “After confirmation QuoteWise will run a fresh Google Places search and
  immediately start the all-vendor campaign.”;
- badge **Google Places · live discovery after review**;
- destinazione role-player mascherata dal runtime;
- diagramma **Call 1 · explore** → quote barriers → **Call 2 · negotiate**;
- verità: tutti i numeri Google restano intatti nel DB ma non saranno chiamati;
- checkbox inizialmente non selezionata:

> I reviewed the job and authorize exactly two live calls to the configured human
> role-player: one exploratory call in the first batch and one automatic grounded
> negotiation callback after all quote batches. No Google business will be called.

Il bottone è disabilitato anche quando `google_places_configured=false`. È
**Confirm, discover vendors and start calls**. Genera un UUID per questo
click e invia una sola richiesta:

```http
POST /api/jobs/{job_id}/launch
Content-Type: application/json

{
  "authorize_demo_calls": true,
  "idempotency_key": "<uuid-stabile-per-questo-click>"
}
```

Non inviare state, query, vendor, company IDs, count, parallel o numeri telefonici.
Disabilita subito il bottone. Riusa lo stesso UUID solo per un retry di trasporto
della stessa azione; non generare un secondo launch.

Durante la risposta mostra fasi reali, senza percentuali inventate:

```text
Validating confirmed scope
Searching Google Places live
Promoting every callable Places result
Selecting the role-play identity
Starting synchronous quote batches
```

La risposta è:

```ts
type LaunchResponse = {
  launched: true;
  redirect: string;
  discovery: {
    provider: "google_places";
    live_api: true;
    generated_at: string;
    raw_results: number;
    callable_vendors: number;
  };
  live_company: {id: string; name: string};
  run: StartRunResponse;
};
```

Quando arriva, mostra per un istante:
**Fresh Google Places search complete · {callable_vendors} callable vendors**,
poi naviga immediatamente a `response.redirect`, normalmente `/job/:jobId/calls`.

Gestisci:

- 409: missing consent, documento assente, launch già esistente o job archived;
- 422: errori esatti della spec;
- 502: errore della chiamata live Google Places;
- 503: API key/config provider assente;
- 404: nessun business Google callable o target richiesto non trovato.

Non fare fallback a risultati cached e non mostrare una finta discovery riuscita.

## 3. Calls — `/job/:jobId/calls`

Questa pagina è solo osservativa durante la demo. Non mostrare Start calls, vendor
selection, Call List, phone inputs, callback button o retry automatico.

Poll:

- `GET /api/jobs/:jobId/call-queue` ogni 750–1000 ms mentre ci sono chiamate o
  transcript in streaming;
- `GET /api/jobs/:jobId/calls` con la stessa cadenza;
- `GET /api/jobs/:jobId/quotes` dopo ogni Call terminale;
- Job, Intake Form e Report dopo ogni barrier.

Interrompi polling quando la tab è nascosta e refetch subito al ritorno.

### Receipt della discovery reale

In alto mostra una card non interattiva:

**Market sourced live from Google Places**

Usa `job.demo_mode.discovery` e `job.launch` per data, stato, conteggio e `live_api`.
Spiega che le identità sono reali ma la telefonia N−1 è sintetica. Non mostrare numeri
completi. Evidenzia l'unica identità associata all'umano con:
**Consenting role-player · real call routed to configured phone**.

### Sticky KPI

Tre card sempre in cima, guidate solo da `queue.summary`:

1. **Current best offer** — nome, totale, binding e red flags.
2. **Observed offer range** — low–high e count.
3. **Called** — called/total e calling now.

Non ricalcolare best o range in JavaScript.

### Batch timeline

Mostra quote batch `1..quote_batch_count` e il batch finale
`auto_negotiation_batch`. Ogni batch visualizza phase, status, completed/total,
knowledge version e hard barrier.

Caption:
“Every conversation in this batch sees the same frozen facts. Knowledge advances
only after every call in the batch is terminal.”

L'umano deve apparire:

- nel batch 1: **Live human · exploratory quote**;
- nel batch finale: **Live human · grounded negotiation**.

La prima chiamata non mostra competing leverage. La seconda mostra la knowledge finale,
l'offerta iniziale dello stesso vendor e gli esatti `leverage_quote_ids` consentiti.

### Vendor e attempts

Una riga stabile per Company; gli attempt sono annidati. Mostra nome Places, rating,
indirizzo, stato, batch, knowledge version, initial/negotiated total, flags e attempt count.

Badge obbligatori:

- synthetic: **Synthetic demo-market chat · Google business not contacted**;
- demo quote: **Live human · exploratory quote**;
- demo negotiate: **Live human · negotiation callback**;
- genuine non-demo voice only: **Real vendor voice call**.

Non etichettare mai un transcript sintetico come chiamata reale.

### Transcript progressivi

Usa sempre `Call.id` come chiave. Aggiorna quando aumentano
`transcript_turn_count`, `last_transcript_at` o la lunghezza del transcript. Non
generare messaggi nel browser.

Una Quote deve avere lo stesso `call_id` del transcript che l'ha prodotta. Mentre
`transcript_streaming=true`, mostra **Quote pending conversation completion**. La quote
compare solo dopo Call terminale.

Per synthetic:
“AI-generated demo conversation. This Google business was not contacted; no audio exists.”

Per role-play:
“Consenting human role-play. This person does not represent the displayed Google business.”

Se il provider live non espone turni parziali, mostra
**Live call in progress · transcript will appear when finalized**; non inventare turni.

### Audio

Se `has_audio=true`, fetch autenticato di `API_BASE + audio_url`, conversione Blob,
object URL per `<audio controls>` e revoke su unmount. Nascondi audio per synthetic.

## 4. Compare — `/job/:jobId/compare`

Carica `GET /api/jobs/:jobId/report`. Non ricalcolare ranking o preferred nel client.

### Mappa dei preventivi

Implementa una vera mappa responsive sopra il ranking usando React Leaflet con tile
OpenStreetMap, oppure MapLibre se è già installato. Non richiedere una seconda API key e
non geocodificare indirizzi nel browser: usa esclusivamente `report.map.points`, le cui
coordinate provengono dalla discovery live Google Places.

Fit bounds su tutti i point validi. Se ce n'è uno solo usa zoom 13; se non esistono
coordinate mostra un empty state, non coordinate inventate.

Ogni pin normale deve mostrare direttamente il prezzo in una pill:

```text
$2,473
```

Colori:

- verde: trusted senza high-risk flags;
- ambra: quote non-binding o con warning;
- grigio: no verified quote;
- preferred: pin più grande verde scuro con icona **stella** e prezzo.

Il popup del pin mostra nome, prezzo finale, rank, binding/trusted, rating, numero di
recensioni, red flags, indirizzo e link Google Maps. Click sul pin evidenzia e scrolla
la stessa ranked card; click sulla ranked card centra/apre il pin. Mantieni accessibilità
keyboard e aria-label come “Preferred vendor, {name}, {price}”.

Mostra una legenda permanente:

```text
★ Preferred offer   ● Verified quote   ● Warning   ● No verified quote
```

Contratto backend:

```ts
type ReportMapPoint = {
  company_id: string;
  company_name: string;
  latitude: number;
  longitude: number;
  address: string;
  google_maps_url: string;
  rating: number | null;
  review_count: number | null;
  rank: number;
  outcome: string;
  final_total: number | null;
  price_label: string;
  trusted: boolean;
  preferred: boolean;
  red_flag_count: number;
};

type ReportMap = {
  points: ReportMapPoint[];
  preferred_company_id: string;
  coordinate_source: "google_places_live_discovery";
};
```

### Ranking e recommendation

Sotto la mappa mostra benchmark, recommendation backend e ranked cards con initial,
negotiated, final, savings, binding, itemised fees, conditions, flags e evidence.
Collega ogni card al transcript e agli audio autenticati. Il preferred della mappa deve
essere esattamente `report.map.preferred_company_id`, mai il minimo calcolato dal client.

Nel demo non scrivere “book this Google business”: specifica se il risultato è role-play
o synthetic. Celebra **New best offer, verified in the live negotiation** solo quando il
preferred è il live role-player e la negotiated quote è evidence-, grounding- e
itemization-verified.

## Contratti principali

```ts
type Job = {
  id: string;
  vertical: string;
  area_code: string;
  spec: Record<string, unknown>;
  spec_source: string;
  confirmed: boolean;
  archived?: boolean;
  documents?: unknown[];
  discovered_questions?: unknown[];
  knowledge_version?: number;
  follow_up_plan?: unknown[];
  launch?: {
    idempotency_key: string;
    status: string;
    created_at?: string;
    google_places_generated_at?: string;
    selected_company_id?: string;
    run_id?: string;
    error?: string;
  };
  demo_mode?: {
    active: boolean;
    roleplay: true;
    status: string;
    session_id: string;
    live_company_id: string;
    live_company_name: string;
    live_company_google_place_id?: string;
    auto_negotiate: boolean;
    demo_calls_authorized?: boolean;
    discovery?: {
      provider: "google_places";
      state: string;
      query: string;
      target: number;
      status: string;
      required_at_launch: true;
      live_api?: boolean;
      generated_at?: string;
      result_count?: number;
    };
  };
};

type StartRunResponse = {
  started: boolean;
  run_id: string;
  phase: "quote";
  total: number;
  total_calls: number;
  batch_size: number;
  batch_count: number;
  quote_batch_count: number;
  auto_negotiation_batch: number;
  auto_negotiation_status: string;
  demo_roleplay: true;
  demo_calls_authorized: true;
  live_company_id: string;
  live_destination: "configured_demo_phone";
};
```

Mantieni tolleranti i type per campi additivi. Lo stato server è autorevole.

## Errori e sicurezza

- 401: logout locale e login.
- 404: job/vendor/evidence/Places result mancante.
- 409: intake incompleto, consenso mancante, launch duplicato, archived o run attivo.
- 422: errori spec accanto ai campi.
- 502: Google Places, tunnel o provider failure; non mostrare fake success.
- 503: chiave/config mancante.
- Stato provider incerto: banner rosso **Automatic redial suppressed**.
- Nessun browser phone input.
- Nessun client-side call retry.
- Nessun POST manuale per la callback finale.
- Nessun dato sintetico senza badge permanente.
- Nessun workflow GitHub.

## Acceptance checklist

- Esistono solo Intake, Spec, Calls e Compare.
- La Call List page è rimossa e i vecchi link redirigono a Calls.
- Documento, form e voice opzionale confluiscono nella stessa spec.
- La review invia una singola `/launch` con consenso e idempotency key.
- Dopo review avviene una nuova chiamata reale Google Places, mai un riuso cached.
- Tutte le identità e coordinate derivano dalla response Places appena salvata.
- Nessun telefono Google viene chiamato.
- N−1 transcript sintetici avanzano progressivamente dalle stesse Call persistite.
- Il role-player è nel primo batch come Explorer e nel batch finale come Closer.
- La callback parte solo dal backend dopo tutte le barriere.
- Best offer, range, called/total e knowledge version sono realtime.
- Compare mostra pin con prezzi e un unico pin preferred con stella.
- Pin e ranked card sono sincronizzati.
- Recommendation e preferred provengono solo dal backend.
