import { memo, useMemo } from "react";
import {
  BaseEdge,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  getBezierPath,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  BATTERY_STATE_LABELS,
  GRID_STATE_LABELS,
  activeFlowIds,
  formatNumber,
} from "../lib/energyData";

const EnergyNode = memo(function EnergyNode({ data }) {
  return (
    <div
      className={`energy-flow-node energy-flow-node--${data.kind}`}
      aria-label={`${data.label}: ${data.value}${data.status ? `, ${data.status}` : ""}`}
    >
      <Handle
        id="top-target"
        type="target"
        position={Position.Top}
        className="energy-flow-handle"
        style={{ left: "38%" }}
      />
      <Handle
        id="top-source"
        type="source"
        position={Position.Top}
        className="energy-flow-handle"
        style={{ left: "62%" }}
      />
      <Handle
        id="left-target"
        type="target"
        position={Position.Left}
        className="energy-flow-handle"
        style={{ top: "62%" }}
      />
      <Handle
        id="left-source"
        type="source"
        position={Position.Left}
        className="energy-flow-handle"
        style={{ top: "38%" }}
      />
      <span className="energy-flow-node__icon" aria-hidden="true">
        {data.icon}
      </span>
      <span className="energy-flow-node__copy">
        <strong>{data.label}</strong>
        <small>{data.value}</small>
        {data.status && <em>{data.status}</em>}
      </span>
      <Handle
        id="right-target"
        type="target"
        position={Position.Right}
        className="energy-flow-handle"
        style={{ top: "62%" }}
      />
      <Handle
        id="right-source"
        type="source"
        position={Position.Right}
        className="energy-flow-handle"
        style={{ top: "38%" }}
      />
      <Handle
        id="bottom-target"
        type="target"
        position={Position.Bottom}
        className="energy-flow-handle"
        style={{ left: "62%" }}
      />
      <Handle
        id="bottom-source"
        type="source"
        position={Position.Bottom}
        className="energy-flow-handle"
        style={{ left: "38%" }}
      />
    </div>
  );
});

const BoundaryNode = memo(function BoundaryNode() {
  return <span className="energy-flow-boundary" aria-hidden="true" />;
});

function EnergyEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  data,
}) {
  const [bezierPath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature: 0.35,
  });
  const paths = {
    "outer-left": `M ${sourceX},${sourceY} C ${sourceX - 92},${sourceY + 18} ${targetX - 46},${targetY - 78} ${targetX},${targetY}`,
    "outer-right": `M ${sourceX},${sourceY} C ${sourceX + 92},${sourceY + 18} ${targetX + 46},${targetY - 78} ${targetX},${targetY}`,
    "inner-left": `M ${sourceX},${sourceY} C ${sourceX},${sourceY - 54} ${targetX - 34},${targetY} ${targetX},${targetY}`,
    "inner-right": `M ${sourceX},${sourceY} C ${sourceX},${sourceY - 54} ${targetX + 34},${targetY} ${targetX},${targetY}`,
    "lower-forward": `M ${sourceX},${sourceY} C ${sourceX},${sourceY + 18} ${targetX},${targetY + 18} ${targetX},${targetY}`,
    "lower-reverse": `M ${sourceX},${sourceY} C ${sourceX},${sourceY + 38} ${targetX},${targetY + 38} ${targetX},${targetY}`,
  };
  const path = paths[data.route] || bezierPath;
  const active = data.active !== false;

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke: data.colour,
          strokeWidth: 1.8,
          opacity: active ? 0.48 : 0.12,
        }}
      />
      {/* Always mounted, hidden by CSS when idle: a removed <animateMotion>
          does not restart when it is inserted again (Chrome), which used to
          freeze the particles after switching stations. */}
      <g
        className={`energy-flow-particles${active ? "" : " is-idle"}`}
        aria-hidden="true"
      >
        {[0, 1, 2].map((particle) => (
          <g key={particle}>
            <circle r="6" fill={data.colour} opacity="0.14" />
            <circle r="2.8" fill={data.colour} />
            <animateMotion
              path={path}
              dur={`${data.duration}s`}
              begin={`${(-data.duration * particle) / 3}s`}
              repeatCount="indefinite"
            />
          </g>
        ))}
      </g>
    </>
  );
}

const nodeTypes = { energy: EnergyNode, boundary: BoundaryNode };
const edgeTypes = { energy: EnergyEdge };

const EDGE_DIRECTIONS = {
  "solar-home": "solar_to_home",
  "solar-battery": "solar_to_battery",
  "solar-grid": "solar_to_grid",
  "battery-home": "battery_to_home",
  "battery-grid": "battery_to_grid",
  "grid-home": "grid_to_home",
  "grid-battery": "grid_to_battery",
};

function buildNodes(display, telemetry, states) {
  // ponytail: initialWidth/initialHeight only stand in for the frames before React Flow has
  // measured the node (they match the CSS box, 150x64). Without them a re-adopted node has no
  // dimensions at all, so React Flow hides it - and every edge attached to it - until the
  // ResizeObserver happens to fire again.
  return [
    {
      id: "solar",
      type: "energy",
      initialWidth: 150,
      initialHeight: 64,
      position: { x: 185, y: 0 },
      data: {
        kind: "solar",
        icon: "☀",
        label: "Solar",
        value: `${formatNumber(display?.solar_power_kw)} kW`,
      },
    },
    {
      id: "home",
      type: "energy",
      initialWidth: 150,
      initialHeight: 64,
      position: { x: 185, y: 118 },
      data: {
        kind: "home",
        icon: "⌂",
        label: "Home",
        value: `${formatNumber(display?.home_usage_kw)} kW`,
      },
    },
    {
      id: "battery",
      type: "energy",
      initialWidth: 150,
      initialHeight: 64,
      position: { x: 20, y: 236 },
      data: {
        kind: "battery",
        icon: "▯",
        label: "Battery",
        value: `${formatNumber(telemetry?.soc_pct, 0)}% · ${formatNumber(display?.battery_power_kw)} kW`,
        status: BATTERY_STATE_LABELS[states?.battery],
      },
    },
    {
      id: "grid",
      type: "energy",
      initialWidth: 150,
      initialHeight: 64,
      position: { x: 350, y: 236 },
      data: {
        kind: "grid",
        icon: "♜",
        label: "Grid",
        value: `${formatNumber(display?.grid_power_kw)} kW`,
        status: GRID_STATE_LABELS[states?.grid],
      },
    },
    {
      id: "boundary-top-left",
      type: "boundary",
      initialWidth: 1,
      initialHeight: 1,
      position: { x: -32, y: -14 },
      data: {},
      selectable: false,
    },
    {
      id: "boundary-bottom-right",
      type: "boundary",
      initialWidth: 1,
      initialHeight: 1,
      position: { x: 552, y: 344 },
      data: {},
      selectable: false,
    },
  ];
}

const edge = ({
  id,
  source,
  target,
  sourceHandle,
  targetHandle,
  colour,
  duration = 3,
  route,
}) => ({
  id,
  source,
  target,
  sourceHandle,
  targetHandle,
  type: "energy",
  data: { colour, duration, route },
  markerEnd: { type: MarkerType.ArrowClosed, color: colour },
  zIndex: 1,
});

const edges = [
  edge({
    id: "solar-home",
    source: "solar",
    target: "home",
    sourceHandle: "bottom-source",
    targetHandle: "top-target",
    colour: "#e8a20a",
    duration: 2.2,
  }),
  edge({
    id: "solar-battery",
    source: "solar",
    target: "battery",
    sourceHandle: "left-source",
    targetHandle: "left-target",
    colour: "#e8a20a",
    duration: 2.8,
    route: "outer-left",
  }),
  edge({
    id: "solar-grid",
    source: "solar",
    target: "grid",
    sourceHandle: "right-source",
    targetHandle: "right-target",
    colour: "#e8a20a",
    duration: 3.1,
    route: "outer-right",
  }),
  edge({
    id: "battery-home",
    source: "battery",
    target: "home",
    sourceHandle: "top-source",
    targetHandle: "left-target",
    colour: "#13a34b",
    duration: 2.6,
    route: "inner-left",
  }),
  edge({
    id: "battery-grid",
    source: "battery",
    target: "grid",
    sourceHandle: "bottom-source",
    targetHandle: "bottom-target",
    colour: "#13a34b",
    duration: 3.4,
    route: "lower-forward",
  }),
  edge({
    id: "grid-home",
    source: "grid",
    target: "home",
    sourceHandle: "top-source",
    targetHandle: "right-target",
    colour: "#6651ec",
    duration: 2.9,
    route: "inner-right",
  }),
  edge({
    id: "grid-battery",
    source: "grid",
    target: "battery",
    sourceHandle: "bottom-source",
    targetHandle: "bottom-target",
    colour: "#6651ec",
    duration: 3.8,
    route: "lower-reverse",
  }),
];

export default function EnergyFlow({ overview }) {
  const view = overview?.data;
  const display = view?.display;
  const telemetry = view?.telemetry;
  const states = view?.states;
  const directions = view?.flow.active_directions;
  // ponytail: React Flow re-creates a node's internals - dropping its measured size and
  // handle bounds - whenever the node object is a new reference (adoptUserNodes/parseHandles).
  // A node without dimensions is rendered `visibility: hidden`, and the ResizeObserver only
  // re-measures it when its box actually changes, so switching to a station with the same
  // text width left the whole diagram blank until an F5. Memoise on the values actually
  // drawn, so unrelated renders (the station switch itself, the AI card landing, a toast)
  // never re-adopt the graph.
  const nodes = useMemo(
    () => buildNodes(display, telemetry, states),
    [display, telemetry, states],
  );
  // Every edge stays on the canvas (topology never blank-outs); only the ones
  // the slot is actually using animate.
  const flowEdges = useMemo(() => {
    const activeIds = new Set(activeFlowIds(directions, EDGE_DIRECTIONS));
    return edges.map((item) => ({
      ...item,
      data: { ...item.data, active: activeIds.has(item.id) },
    }));
  }, [directions]);

  return (
    <div
      className="energy-flow-graph"
      role="img"
      aria-label="Live energy flowing from solar to the home, battery and grid"
    >
      <ReactFlow
        nodes={nodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.04 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
      />
      <div className="energy-flow-legend" aria-label="Energy flow legend">
        <span className="is-solar">Solar</span>
        <span className="is-battery">Battery</span>
        <span className="is-grid">Grid</span>
      </div>
    </div>
  );
}
