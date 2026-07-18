Dobbiamo essere in grado di cambiare completamente il dominio
della nostra applicazione tramite semplici fogli di specifica.
Ad esempio l'estimator deve essere in grado di essere un professional
estimator per i trasporti e per i sistemi di HVAC sulla base della
configurazione.

Per fare un MVP iniziale dobbiamo concentrarci su un dominio solo,
in particolare ci concentriamo sul dominio dei plumbers.

Il foglio di specifica, specifica che l'estimator deve essere un professional
estimator del dominio a cui appartiene (ad esempio plumbers).

Nel foglio di specifica una parte deve specificare le domande 
pre-pronte del form e poi un codice dell'area


Alla fine della chiamata vanno aggiunte altre informazioni utili
per stimare meglio il prezzo. Queste le passiamo all'utente e le
mettiamo dentro il database per introdurle in modo fisso dentro il form
la prossima volta.

Quando costruiamo il form dobbiamo prendere
- quelle di base dal foglio di specifica
- dal database raccolgi quelle raccolte da chiamate precedenti.

Un'altra cosa molto importante e' che il foglio di configurazione 
per ogni area deve essere scrivibile dall'AI.

Intanto partiamo con la scrittura del modulo Estimator, successivamente
penseremo ai prossimi. Ed infine al web frontend scritto interamente
con Lovable.


Queste sono le API a cui ho accesso per questa challenge:

```
ElevenLabs logo
elevenlabs.io

Premium AI voice generation and text-to-speech. Create realistic voiceovers and audio content.
Your credit
Redeem ElevenLabs credit
Emdash logo

Open-source desktop app to run coding agents in parallel, local and remote.
Download EmdashDownload Emdash Onboarding Guide
Lovable logo
Lovable
lovable.dev

AI-powered full-stack web application builder. Create complete web apps from natural language descriptions.
Your credit
OpenAI logo
openai.com
One-time

Build with OpenAI APIs including GPT and multimodal models. Complete the short project form to claim one code.
Tavily logo
www.tavily.com
Shared

Search API for AI agents and RAG workflows. Use Tavily to add reliable web search and retrieval to your hackathon project.

Use this code
••••••••••••

Available to all participants
Download Tavily Hacker Guide
Woz logo
wozcode.com
Shared

AI coding performance tools for Claude Code, Cursor, and VS Code.

Use this code
••••••••••••

Available to all participants
Open Woz documentation
project deliverables
```
