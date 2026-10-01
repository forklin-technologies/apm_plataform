import { semearEscolaPiloto } from "@/db/seed-dados";
import { encerrarDb } from "@/db/cliente";

semearEscolaPiloto()
  .then((e) => console.log(`Escola criada: /apm/${e.slug}`))
  .catch((e) => { console.error(e); process.exitCode = 1; })
  .finally(encerrarDb);
