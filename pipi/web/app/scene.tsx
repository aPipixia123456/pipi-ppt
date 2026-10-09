"use client";
import type { Element, Slide } from "./types";

export function Scene({
  slide,
  selected,
  onSelect,
}: {
  slide: Slide;
  selected?: string;
  onSelect?: (id: string) => void;
}) {
  return (
    <svg
      className="scene"
      viewBox="0 0 1280 720"
      role="img"
      aria-label={slide.name}
    >
      <rect width="1280" height="720" fill={slide.background} />
      {slide.elements.map((element) => (
        <g
          key={element.id}
          onClick={() => onSelect?.(element.id)}
          style={{ cursor: onSelect ? "pointer" : "default" }}
        >
          <SceneElement element={element} />
          {selected === element.id && (
            <rect
              x={element.x}
              y={element.y}
              width={element.w}
              height={element.h}
              fill="none"
              stroke="#7C5CE5"
              strokeWidth="3"
              strokeDasharray="8 5"
            />
          )}
        </g>
      ))}
    </svg>
  );
}

function SceneElement({ element: e }: { element: Element }) {
  const box = { x: e.x, y: e.y, width: e.w, height: e.h };
  if (e.type === "shape")
    return e.shape === "ellipse" ? (
      <ellipse
        cx={e.x + e.w / 2}
        cy={e.y + e.h / 2}
        rx={e.w / 2}
        ry={e.h / 2}
        fill={e.fill}
      />
    ) : (
      <rect {...box} fill={e.fill} />
    );
  if (e.type === "image")
    return e.asset_id || e.builtin_asset ? (
      <image
        {...box}
        href={
          e.asset_id
            ? `/api/assets/${e.asset_id}`
            : `/api/template-assets/${e.builtin_asset}`
        }
        preserveAspectRatio={
          e.image_fit === "fill"
            ? "none"
            : e.image_fit === "cover"
              ? "xMidYMid slice"
              : "xMidYMid meet"
        }
      />
    ) : (
      <g>
        <rect {...box} fill="#EFF0F6" rx="8" />
        <path
          d={`M${e.x + e.w * 0.3} ${e.y + e.h * 0.65} l${e.w * 0.16} ${-e.h * 0.3} l${e.w * 0.14} ${e.h * 0.18} l${e.w * 0.1} ${-e.h * 0.12}`}
          fill="none"
          stroke="#C4C5D5"
          strokeWidth="6"
        />
      </g>
    );
  if (e.type === "text")
    return (
      <foreignObject {...box}>
        <div
          style={{
            fontFamily: `'${e.font}', sans-serif`,
            fontSize: e.font_size,
            lineHeight: e.line_height || 1.25,
            textAlign: e.align || "left",
            display: "flex",
            alignItems:
              e.valign === "middle"
                ? "center"
                : e.valign === "bottom"
                  ? "flex-end"
                  : "flex-start",
            fontWeight: e.bold ? 700 : 400,
            color: e.color,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            overflow: "hidden",
            height: "100%",
          }}
        >
          <span style={{ width: "100%" }}>{e.text}</span>
        </div>
      </foreignObject>
    );
  if (e.type === "table")
    return (
      <foreignObject {...box}>
        <table
          className="slide-table"
          style={{ fontSize: e.font_size, color: e.color }}
        >
          <tbody>
            {e.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </foreignObject>
    );
  const high = Math.max(0, ...e.values);
  const low = Math.min(0, ...e.values);
  const scale = (e.h * 0.65) / Math.max(1, high - low);
  const baseline = e.y + e.h * 0.1 + high * scale;
  const bar = e.w / Math.max(1, e.values.length);
  return (
    <g>
      {e.values.map((value, i) => (
        <g key={i}>
          <rect
            x={e.x + i * bar + bar * 0.15}
            y={baseline - Math.max(0, value) * scale}
            width={bar * 0.7}
            height={Math.abs(value) * scale}
            fill={e.fill}
          />
          <text
            x={e.x + i * bar + bar * 0.5}
            y={e.y + e.h * 0.94}
            textAnchor="middle"
            fontSize={Math.min(e.font_size, 24)}
            fill={e.color}
          >
            {e.labels[i]}
          </text>
          <text
            x={e.x + i * bar + bar * 0.5}
            y={
              value >= 0
                ? baseline - value * scale - 8
                : baseline - value * scale + 22
            }
            textAnchor="middle"
            fontSize="20"
            fill={e.color}
          >
            {value}
          </text>
        </g>
      ))}
    </g>
  );
}
