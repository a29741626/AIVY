import { useEffect, useRef } from "react";
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { init, use as registerEChartsComponents } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

registerEChartsComponents([
  LineChart,
  GridComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export default function PriceHistoryChart({
  series = { labels: [], values: [] },
}) {
  const chartRef = useRef(null);

  useEffect(() => {
    if (!chartRef.current) return undefined;

    const chart = init(chartRef.current, undefined, { renderer: "canvas" });
    // Mark the newest quote instead of the last bucket: trailing hours are still null.
    const latest = series.values.reduce(
      (found, value, index) => (value == null ? found : index),
      -1,
    );
    const values = series.values.map((value, index) =>
      index === latest
        ? {
            value,
            symbolSize: 8,
            itemStyle: { color: "#1677ff", borderColor: "#fff", borderWidth: 2 },
          }
        : value,
    );

    chart.setOption({
      animationDuration: 650,
      grid: { top: 8, right: 10, bottom: 24, left: 34 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#142338",
        borderWidth: 0,
        padding: [7, 9],
        textStyle: { color: "#fff", fontSize: 11 },
        formatter: ([point]) =>
          `${point.axisValue}<br/><b>${
            point.value == null ? "no quote" : `${Number(point.value).toFixed(1)} c/kWh`
          }</b>`,
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: series.labels,
        axisLine: { lineStyle: { color: "#dce6ee" } },
        axisTick: { show: false },
        axisLabel: {
          color: "#718093",
          fontSize: 9,
          interval: 1,
          margin: 8,
        },
      },
      yAxis: {
        type: "value",
        scale: true,
        splitNumber: 3,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: "#718093",
          fontSize: 9,
          formatter: "{value}¢",
        },
        splitLine: { lineStyle: { color: "#edf2f6" } },
      },
      series: [
        {
          name: "Electricity price",
          type: "line",
          smooth: 0.35,
          showSymbol: false,
          symbol: "circle",
          lineStyle: { color: "#1677ff", width: 2.5 },
          itemStyle: { color: "#1677ff" },
          areaStyle: { color: "rgba(22, 119, 255, 0.12)" },
          data: values,
        },
      ],
    });

    const resizeObserver = new ResizeObserver(() => chart.resize());
    resizeObserver.observe(chartRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.dispose();
    };
  }, [series]);

  return (
    <div
      ref={chartRef}
      className="price-history-chart"
      role="img"
      aria-label="Electricity price for each hour of today"
    />
  );
}
