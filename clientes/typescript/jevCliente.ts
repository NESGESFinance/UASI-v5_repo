/**
 * Cliente de referencia JEV → UASI para Node.js ≥ 18 (sin dependencias: `node:crypto` + `fetch`).
 *
 * Protocolo de firma (idéntico al servidor Python):
 *   firma = HMAC-SHA256(clave, canonico(cuerpo sin firma_hmac / firma_respuesta)) en hex
 *   canonico = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
 *
 * Diferencias JS ↔ Python que este módulo resuelve:
 *  - Números no enteros: se formatean como `repr(float)` de Python (p. ej. 1e-05, 1e+16).
 *  - Verificación de respuestas: el JSON se re-parsea conservando el literal numérico original
 *    (Python serializa 12.0 como "12.0" y JS lo perdería como 12).
 * Limitaciones documentadas: claves con caracteres fuera del BMP (orden UTF-16 vs code points) y
 * strings con sustitutos sueltos. Para importes RWA se recomienda enviar strings decimales.
 */
import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";

export type ValorJSON = null | boolean | number | string | ValorJSON[] | { [k: string]: ValorJSON };

const CAMPOS_FIRMA = new Set(["firma_hmac", "firma_respuesta"]);

/** Literal numérico preservado tal como lo emitió el servidor. */
class NumeroCrudo {
  readonly literal: string;
  constructor(literal: string) {
    this.literal = literal;
  }
}

type ValorCrudo = null | boolean | string | NumeroCrudo | ValorCrudo[] | { [k: string]: ValorCrudo };

/** Equivalente a repr(float) de Python para un número JS. */
export function numeroPython(n: number): string {
  if (!Number.isFinite(n)) throw new Error("JSON no admite NaN/Infinity");
  if (Number.isInteger(n) && Math.abs(n) < 1e21) return String(n); // JSON.stringify lo emite como entero ⇒ int en Python
  const [mantisa, expTxt] = n.toExponential().split("e"); // dígitos mínimos de ida y vuelta
  const exp = Number(expTxt);
  const signo = mantisa.startsWith("-") ? "-" : "";
  const digitos = mantisa.replace("-", "").replace(".", "");
  if (exp >= -4 && exp < 16) {
    if (exp < 0) return `${signo}0.${"0".repeat(-exp - 1)}${digitos}`;
    const entero = digitos.padEnd(exp + 1, "0");
    const resto = digitos.slice(exp + 1);
    return `${signo}${entero.slice(0, exp + 1)}.${resto || "0"}`;
  }
  const m = digitos.length > 1 ? `${digitos[0]}.${digitos.slice(1)}` : digitos;
  const e = Math.abs(exp).toString().padStart(2, "0");
  return `${signo}${m}e${exp < 0 ? "-" : "+"}${e}`;
}

function cadena(s: string): string {
  return JSON.stringify(s); // mismas secuencias de escape que json.dumps(ensure_ascii=False)
}

/** JSON canónico compatible con `json_canonico` del servidor. */
export function canonico(v: ValorJSON | ValorCrudo): string {
  if (v === null) return "null";
  if (v instanceof NumeroCrudo) return v.literal;
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return numeroPython(v);
  if (typeof v === "string") return cadena(v);
  if (Array.isArray(v)) return `[${v.map((x) => canonico(x)).join(",")}]`;
  const obj = v as { [k: string]: ValorJSON | ValorCrudo };
  const claves = Object.keys(obj).sort();
  return `{${claves.map((k) => `${cadena(k)}:${canonico(obj[k])}`).join(",")}}`;
}

function sinFirma<T extends { [k: string]: unknown }>(cuerpo: T): { [k: string]: unknown } {
  const copia: { [k: string]: unknown } = {};
  for (const k of Object.keys(cuerpo)) if (!CAMPOS_FIRMA.has(k)) copia[k] = cuerpo[k];
  return copia;
}

export function firmar(cuerpo: { [k: string]: unknown }, clave: string): string {
  const mensaje = canonico(sinFirma(cuerpo) as ValorJSON | ValorCrudo);
  return createHmac("sha256", clave).update(mensaje, "utf8").digest("hex");
}

/** Parser JSON mínimo que conserva los literales numéricos (para verificar respuestas). */
export function parsearConservandoNumeros(texto: string): ValorCrudo {
  let i = 0;
  const espacios = () => {
    while (i < texto.length && " \t\n\r".includes(texto[i])) i++;
  };
  const valor = (): ValorCrudo => {
    espacios();
    const c = texto[i];
    if (c === "{") {
      i++;
      const obj: { [k: string]: ValorCrudo } = {};
      espacios();
      if (texto[i] === "}") { i++; return obj; }
      for (;;) {
        espacios();
        const k = valor();
        if (typeof k !== "string") throw new SyntaxError(`Clave no textual en ${i}`);
        espacios();
        if (texto[i++] !== ":") throw new SyntaxError(`Se esperaba ':' en ${i}`);
        obj[k] = valor();
        espacios();
        const sep = texto[i++];
        if (sep === "}") return obj;
        if (sep !== ",") throw new SyntaxError(`Se esperaba ',' o '}' en ${i}`);
      }
    }
    if (c === "[") {
      i++;
      const arr: ValorCrudo[] = [];
      espacios();
      if (texto[i] === "]") { i++; return arr; }
      for (;;) {
        arr.push(valor());
        espacios();
        const sep = texto[i++];
        if (sep === "]") return arr;
        if (sep !== ",") throw new SyntaxError(`Se esperaba ',' o ']' en ${i}`);
      }
    }
    if (c === '"') {
      const inicio = i++;
      while (texto[i] !== '"') i += texto[i] === "\\" ? 2 : 1;
      i++;
      return JSON.parse(texto.slice(inicio, i)) as string;
    }
    const m = /^-?\d+(\.\d+)?([eE][+-]?\d+)?/.exec(texto.slice(i));
    if (m) { i += m[0].length; return new NumeroCrudo(m[0]); }
    for (const [lit, v] of [["true", true], ["false", false], ["null", null]] as const) {
      if (texto.startsWith(lit, i)) { i += lit.length; return v; }
    }
    throw new SyntaxError(`Token inesperado en ${i}`);
  };
  const resultado = valor();
  espacios();
  if (i !== texto.length) throw new SyntaxError("Contenido sobrante tras el JSON");
  return resultado;
}

/** Verifica `firma_respuesta` sobre el TEXTO de la respuesta (respeta literales numéricos). */
export function verificarTexto(texto: string, clave: string, campo = "firma_respuesta"): boolean {
  const crudo = parsearConservandoNumeros(texto) as { [k: string]: ValorCrudo };
  const recibida = crudo[campo];
  if (typeof recibida !== "string") return false;
  const esperada = Buffer.from(firmar(crudo, clave), "utf8");
  const dada = Buffer.from(recibida.toLowerCase(), "utf8");
  return esperada.length === dada.length && timingSafeEqual(esperada, dada);
}

export interface OpcionesSolicitud {
  metadatos?: { [k: string]: ValorJSON };
  idJev?: string;
  idSolicitud?: string;
  versionJev?: string;
  incluirTimestamp?: boolean;
}

export function construirSolicitud(
  clave: string,
  tipoTarea: string,
  lenguajes: string[],
  datos: { [k: string]: ValorJSON },
  op: OpcionesSolicitud = {},
): { [k: string]: ValorJSON } {
  const cuerpo: { [k: string]: ValorJSON } = {
    id_jev: op.idJev ?? `jev-${randomUUID()}`,
    version_jev: op.versionJev ?? "1.0",
    tipo_tarea: tipoTarea,
    lenguajes_destino: lenguajes,
    datos,
    metadatos: op.metadatos ?? {},
    id_solicitud: op.idSolicitud ?? randomUUID(),
  };
  if (op.incluirTimestamp ?? true) cuerpo.timestamp = new Date().toISOString();
  cuerpo.firma_hmac = firmar(cuerpo, clave);
  return cuerpo;
}

export class ClienteJEV {
  private readonly urlBase: string;
  private readonly clave: string;
  private readonly token: string;

  constructor(urlBase: string, claveHmac: string, tokenJwt: string) {
    if (!claveHmac) throw new Error("claveHmac es obligatoria");
    this.urlBase = urlBase.replace(/\/+$/, "");
    this.clave = claveHmac;
    this.token = tokenJwt;
  }

  private async post(ruta: string, cuerpo: unknown): Promise<string> {
    const r = await fetch(this.urlBase + ruta, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.token}` },
      body: JSON.stringify(cuerpo),
    });
    const texto = await r.text();
    if (!r.ok) throw new Error(`UASI respondió HTTP ${r.status}: ${texto}`);
    if (!verificarTexto(texto, this.clave)) throw new Error("Firma de respuesta UASI inválida");
    return texto;
  }

  async despachar(
    tipoTarea: string,
    lenguajes: string[],
    datos: { [k: string]: ValorJSON },
    op: OpcionesSolicitud = {},
  ): Promise<{ [k: string]: unknown }> {
    const texto = await this.post("/api/v5/jev-dispatch", construirSolicitud(this.clave, tipoTarea, lenguajes, datos, op));
    return JSON.parse(texto) as { [k: string]: unknown };
  }

  /** La firma global del lote cubre todos los elementos (incluidas sus respuestas firmadas). */
  async despacharLote(solicitudes: { [k: string]: ValorJSON }[]): Promise<{ [k: string]: unknown }> {
    const texto = await this.post("/api/v5/jev-dispatch/lote", { solicitudes });
    return JSON.parse(texto) as { [k: string]: unknown };
  }
}
