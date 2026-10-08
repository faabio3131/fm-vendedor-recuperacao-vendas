import { useId } from "react";

export const SLOGAN = "Sua IA que atende, vende e recupera.";

/** Balão de conversa redondo com as iniciais AV, anel duplo, "digitando" e o selo de IA. Desenhado só com formas
 * (sem fonte), então fica igual em qualquer tela. `compact` tira o anel fino e os pontos para tamanhos pequenos. */
export function BrandMark({ size = 40, compact = false }: { size?: number; compact?: boolean }) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const sil = `sil${uid}`;
  const blu = `blu${uid}`;
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 120 120" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={sil} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#f4f7fa" />
          <stop offset="1" stopColor="#8e98a6" />
        </linearGradient>
        <linearGradient id={blu} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#33d8fb" />
          <stop offset="1" stopColor="#2aa4f0" />
        </linearGradient>
      </defs>
      <path
        d="M60 10A46 46 0 1 1 36 95.2L12 108 18.3 75.4A46 46 0 0 1 60 10Z"
        fill="#020e22"
        stroke={`url(#${sil})`}
        strokeWidth={compact ? 8 : 5}
        strokeLinejoin="round"
      />
      {!compact && <circle cx="60" cy="56" r="36" fill="none" stroke="#2aa4f0" strokeWidth="1.6" opacity=".75" />}
      <g fill="none" strokeWidth={compact ? 10 : 6.5} strokeLinecap="round" strokeLinejoin="round">
        <polyline points={compact ? "33,66 45,32 57,66" : "33,63 45,33 57,63"} stroke={`url(#${sil})`} />
        {!compact && <line x1="37" y1="54" x2="53" y2="54" stroke={`url(#${sil})`} />}
        <polyline points={compact ? "63,32 75,66 87,32" : "63,33 75,63 87,33"} stroke={`url(#${blu})`} />
      </g>
      {!compact && (
        <>
          <circle cx="49" cy="77" r="3.2" fill="#33d8fb" />
          <circle cx="60" cy="77" r="3.2" fill="#33d8fb" opacity=".75" />
          <circle cx="71" cy="77" r="3.2" fill="#33d8fb" opacity=".5" />
        </>
      )}
      <circle cx="98" cy="20" r="13" fill={`url(#${blu})`} stroke="#000610" strokeWidth="3" />
      <path d="M98 12.5 100.2 17.8 105.5 20 100.2 22.2 98 27.5 95.8 22.2 90.5 20 95.8 17.8Z" fill="#000610" />
    </svg>
  );
}

/** Marca com o nome ao lado. O nome é texto de verdade (leitor de tela e busca enxergam). */
export function Brand({ size = 36, tagline = false, stacked = false }: { size?: number; tagline?: boolean; stacked?: boolean }) {
  return (
    <div className={`brand${stacked ? " stacked" : ""}`}>
      <BrandMark size={size} compact={size <= 36} />
      <div>
        <div className="brand-name">
          <span className="brand-a">AtendeVende</span>
          <span className="brand-ia">IA</span>
        </div>
        {tagline && <div className="brand-tag">{SLOGAN}</div>}
      </div>
    </div>
  );
}
