import React from "react";

export default function AlertCarouselCard({ children, index, total, onPrevious, onNext }) {
  const card = React.Children.toArray(children)[0];
  if (!card) return null;

  const controls = (
    <div
      key="alert-carousel-controls"
      style={{
        position: "absolute",
        top: 10,
        right: 12,
        zIndex: 1,
        display: "flex",
        alignItems: "center",
        gap: 4,
        padding: "3px 5px",
        border: "1px solid var(--border)",
        borderRadius: 9,
        background: "var(--panel)",
        boxShadow: "0 1px 3px rgba(26,34,51,.08)",
      }}
    >
      <button
        type="button"
        aria-label="Previous alert"
        title="Previous alert"
        disabled={total < 2}
        onClick={onPrevious}
        style={{
          width: 24,
          height: 24,
          padding: 0,
          border: "none",
          borderRadius: 6,
          background: "transparent",
          color: "var(--text)",
          cursor: total < 2 ? "default" : "pointer",
          opacity: total < 2 ? 0.35 : 1,
          fontSize: 18,
          lineHeight: 1,
        }}
      >
        ‹
      </button>
      <span
        aria-live="polite"
        style={{
          minWidth: 42,
          textAlign: "center",
          color: "var(--dim)",
          fontFamily: "'IBM Plex Mono',monospace",
          fontSize: 10.5,
          whiteSpace: "nowrap",
        }}
      >
        {index + 1} of {total}
      </span>
      <button
        type="button"
        aria-label="Next alert"
        title="Next alert"
        disabled={total < 2}
        onClick={onNext}
        style={{
          width: 24,
          height: 24,
          padding: 0,
          border: "none",
          borderRadius: 6,
          background: "transparent",
          color: "var(--text)",
          cursor: total < 2 ? "default" : "pointer",
          opacity: total < 2 ? 0.35 : 1,
          fontSize: 18,
          lineHeight: 1,
        }}
      >
        ›
      </button>
    </div>
  );

  return (
    <div style={{ position: "relative" }}>
      {React.cloneElement(card, {
        style: {
          ...card.props.style,
          paddingRight: 150,
        },
      })}
      {controls}
    </div>
  );
}
