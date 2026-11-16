import { useEffect, useRef, useState } from "react";
import { Select } from "antd";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import { init, use as registerEChartsComponents } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { getEnergyHistory } from "../api";
import { adelaideToday, hourlySeries } from "../lib/energyData";

registerEChartsComponents([
  LineChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  CanvasRenderer,
]);

const SERIES = [
  { name: "Solar PV", color: "#f5ac00" },
  { name: "Home Load", color: "#2787ee" },
  { name: "Battery", color: "#18a750" },
  { name: "Grid", color: "#684df1" },
];

const RANGE_DATA = {
  Today: {
    unit: "kW",
    labels: [
      "12 AM", "1 AM", "2 AM", "3 AM", "4 AM", "5 AM", "6 AM",
      "7 AM", "8 AM", "9 AM", "10 AM", "11 AM", "12 PM", "1 PM",
      "2 PM", "3 PM", "4 PM", "5 PM", "6 PM", "7 PM", "8 PM",
      "9 PM", "10 PM", "11 PM", "12 AM",
    ],
    values: [
      [0, 0, 0, 0, 0, 0, 0.1, 0.8, 2.8, 5.1, 7.4, 8.7, 9.3, 8.8, 9.1, 7.7, 5.8, 3.2, 1.2, 0.2, 0, 0, 0, 0, 0],
      [1.7, 1.6, 1.5, 1.6, 1.7, 2, 2.9, 3.5, 2.6, 2.1, 1.7, 1.6, 1.8, 1.7, 1.9, 2, 2.3, 3.1, 4.4, 4.9, 4.1, 3.3, 2.7, 2.2, 1.9],
      [-1.4, -1.6, -1.5, -1.4, -1.2, -0.8, 0.1, 0.9, 1.6, 2.1, 2.4, 2.2, 1.9, 1.7, 1.5, 1.1, 0.5, -0.6, -1.8, -2.6, -2.3, -2, -1.8, -1.6, -1.5],
      [3.1, 3, 2.9, 3, 2.8, 2.4, 1.2, -0.5, -3.3, -4.6, -5.1, -4.7, -4.4, -4.6, -4.2, -3.3, -1.7, 0.5, 3.2, 4.7, 4.1, 3.6, 3.2, 2.9, 2.7],
    ],
    peak: "6 PM",
  },
  "This week": {
    unit: "kWh",
    labels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    values: [
      [31, 29, 34, 27, 36, 38, 33],
      [24, 26, 23, 27, 25, 31, 29],
      [7, 5, 9, 2, 10, 6, 4],
      [-9, -6, -12, 1, -14, -10, -7],
    ],
  },
  "This month": {
    unit: "kWh",
    labels: ["Week 1", "Week 2", "Week 3", "Week 4"],
    values: [
      [218, 231, 205, 246],
      [184, 191, 178, 202],
      [39, 44, 31, 48],
      [-61, -72, -49, -81],
    ],
  },
};

export default function EnergyChart({ stationId, refreshKey }) {
  const [range, setRange] = useState("Today");
  const [history, setHistory] = useState(null);
  const [historyError, setHistoryError] = useState("");
  const chartRef = useRef(null);

  useEffect(() => {
    if (range !== "Today") return undefined;

    const controller = new AbortController();

    getEnergyHistory(stationId, adelaideToday(), { signal: controller.signal })
      .then((body) => {
        setHistory(body);
        setHistoryError("");
      })
      .catch((requestError) => {
        if (requestError.name !== "AbortError") {
          setHistoryError(
            requestError.message || "Unable to load today's energy data.",
          );
        }
      });

    return () => controller.abort();
  }, [range, stationId, refreshKey]);

  useEffect(() => {
    const rangeData =
      range === "Today"
        ? history?.data
          ? hourlySeries(history.data.points)
          : null
        : RANGE_DATA[range];

    if (!chartRef.current || !rangeData) return undefined;

    const chart = init(chartRef.current, undefined, { renderer: "canvas" });

    chart.setOption({
      animationDuration: 650,
      color: SERIES.map(({ color }) => color),
      grid: { top: 30, right: 16, bottom: 42, left: 48 },
      legend: {
        bottom: 0,
        itemWidth: 14,
        itemHeight: 3,
        textStyle: { color: "#536273", fontSize: 10 },
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#142338",
        borderWidth: 0,
        padding: [8, 10],
        textStyle: { color: "#fff", fontSize: 11 },
        valueFormatter: (value) =>
          value == null
            ? "No data"
            : `${Number(value).toFixed(1)} ${rangeData.unit}`,
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: rangeData.labels,
        axisLine: { lineStyle: { color: "#becac4" } },
        axisTick: { show: false },
        axisLabel: {
          color: "#718093",
          fontSize: 9,
          interval: range === "Today" ? 5 : 0,
          margin: 9,
        },
        splitLine: {
          show: true,
          interval: range === "Today" ? 5 : 0,
          lineStyle: { color: "#f3f6f4" },
        },
      },
      yAxis: {
        type: "value",
        name: `Energy (${rangeData.unit})`,
        nameTextStyle: { color: "#637386", fontSize: 9, padding: [0, 0, 0, 4] },
        nameGap: 8,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: "#718093", fontSize: 9 },
        splitLine: { lineStyle: { color: "#edf1ef" } },
      },
      series: SERIES.map(({ name, color }, index) => ({
        name,
        type: "line",
        smooth: 0.25,
        showSymbol: false,
        symbol: "circle",
        symbolSize: 5,
        lineStyle: { color, width: 2 },
        itemStyle: { color, borderColor: "#fff", borderWidth: 1 },
        areaStyle: index === 0 ? { color: "rgba(245, 172, 0, 0.10)" } : undefined,
        data: rangeData.values[index],
        markLine:
          index === 0 && rangeData.peak
            ? {
                silent: true,
                symbol: "none",
                lineStyle: { color: "#ef7373", type: "dashed", width: 1 },
                label: { formatter: "Peak", color: "#df6060", fontSize: 9 },
                data: [{ xAxis: rangeData.peak }],
              }
            : undefined,
      })),
    });

    const resizeObserver = new ResizeObserver(() => chart.resize());
    resizeObserver.observe(chartRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.dispose();
    };
  }, [range, history]);

  const note =
    range !== "Today"
      ? "Illustrative demo data"
      : historyError
        ? historyError
        : history
          ? `${history.meta.source.mode === "replay" ? "Replay" : "Recorded"} data · ${history.data.received_points}/${history.data.expected_points} slots · ${history.meta.data_status}`
          : "Loading live data…";

  return (
    <article className="card chart-card">
      <div className="card-title">
        <h2>
          {range === "Today" ? "Today's Energy" : `${range} — Energy Overview`}
        </h2>
        <Select
          aria-label="Energy time range"
          value={range}
          onChange={setRange}
          options={Object.keys(RANGE_DATA).map((option) => ({
            label: option,
            value: option,
          }))}
          size="small"
          style={{ width: 130 }}
        />
      </div>
      <div
        ref={chartRef}
        className="energy-echarts"
        role="img"
        aria-label={`${range}: solar, home load, battery and grid energy chart`}
      />
      <small className="demo-note">{note}</small>
    </article>
  );
}
