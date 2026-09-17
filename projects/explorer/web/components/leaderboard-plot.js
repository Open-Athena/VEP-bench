import * as Plot from "npm:@observablehq/plot@0.6.17";

export function leaderboardBarPlot(rows, {width, color, organizationIcons, formatScore, modelDetails, ariaLabel}) {
  const data = rows.filter((row) => row.score !== null);
  const labelOutside = (row) => row.score <= Math.max(...data.map((row) => row.score)) * 0.08;
  const scoreLabel = (row) => Math.round(row.score * 100).toString();
  return Plot.plot({
    width: Math.max(width, data.length * 100 + 80),
    height: 480,
    marginTop: 35,
    marginRight: 20,
    marginBottom: 110,
    marginLeft: 60,
    ariaLabel,
    x: {domain: data.map((row) => row.key), axis: null, padding: 0.35},
    y: {label: "Score", grid: true, nice: true, ticks: 6, tickFormat: formatScore},
    color,
    marks: [
      Plot.ruleY([0]),
      Plot.barY(data, {
        x: "key", y: "score", fill: "family", rx: 3,
        tip: true, title: modelDetails, ariaLabel: modelDetails
      }),
      Plot.text(data.filter((row) => !labelOutside(row)), {
        x: "key", y: (row) => row.score / 2, text: scoreLabel,
        fill: "white", fontSize: 14, fontWeight: 700
      }),
      Plot.text(data.filter(labelOutside), {
        x: "key", y: "score", text: scoreLabel,
        dy: -10, fontSize: 14, fontWeight: 700
      }),
      Plot.image(data.filter((row) => organizationIcons[row.organization]), {
        x: "key", y: 0, dy: 20, width: 20, height: 20,
        src: (row) => organizationIcons[row.organization].url,
        title: (row) => organizationIcons[row.organization].name
      }),
      Plot.text(data, {
        x: "key", y: 0, dy: 40, lineAnchor: "top", lineWidth: 12,
        text: (row) => row.model.replace(" (", "\n(") + (row.retry_count ? `\n${row.retry_count} retry` : ""), fontSize: 11,
        tip: true, title: modelDetails
      })
    ]
  });
}
