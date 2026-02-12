import { useState, useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useMutation } from "@tanstack/react-query";
import {
  Loader2,
  AlertCircle,
  TrendingUp,
  BarChart3,
  ShieldAlert,
  LineChart,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn, formatPercent } from "@/lib/utils";
import { qlibApi } from "@/api/qlib";
import type {
  OptimizationMethod,
  PortfolioOptimizeResponse,
  RiskDecompositionResponse,
  EfficientFrontierResponse,
} from "@/api/qlib";
import { getErrorMessage } from "@/api/client";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Sort weight entries descending by absolute weight. */
function sortedWeightEntries(
  weights: Record<string, number>,
): Array<[string, number]> {
  return Object.entries(weights).sort(
    ([, a], [, b]) => Math.abs(b) - Math.abs(a),
  );
}

/** Find the maximum absolute weight for bar scaling. */
function maxAbsWeight(weights: Record<string, number>): number {
  const vals = Object.values(weights);
  if (vals.length === 0) return 1;
  return Math.max(...vals.map(Math.abs), 0.01);
}

/** Parse comma/semicolon/space-separated symbols into uppercased array. */
function parseSymbolsFromInput(input: string): string[] {
  return input
    .split(/[,;\s]+/)
    .map((s) => s.trim().toUpperCase())
    .filter((s) => s.length > 0);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface MetricCardProps {
  label: string;
  value: string;
  colorClass?: string | undefined;
}

function MetricCard({ label, value, colorClass }: MetricCardProps) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center p-4">
        <span className="text-xs font-medium text-muted-foreground">
          {label}
        </span>
        <span
          className={cn("mt-1 text-2xl font-bold tabular-nums", colorClass)}
        >
          {value}
        </span>
      </CardContent>
    </Card>
  );
}

interface WeightsTableProps {
  weights: Record<string, number>;
}

function WeightsTable({ weights }: WeightsTableProps) {
  const { t } = useTranslation("common");
  const entries = useMemo(() => sortedWeightEntries(weights), [weights]);
  const maxW = useMemo(() => maxAbsWeight(weights), [weights]);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="pb-2 pr-4 font-medium text-muted-foreground">
              {t("qlib.symbol")}
            </th>
            <th className="pb-2 pr-4 text-right font-medium text-muted-foreground">
              {t("qlib.weight")}
            </th>
            <th className="pb-2 font-medium text-muted-foreground">
              {/* bar column - no header text */}
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([symbol, weight]) => {
            const pct = weight * 100;
            const barWidth = Math.abs(weight) / maxW;
            const isPositive = weight >= 0;
            return (
              <tr
                key={symbol}
                className="border-b border-border/50 last:border-0"
              >
                <td className="py-2 pr-4 font-mono text-xs font-medium">
                  {symbol}
                </td>
                <td
                  className={cn(
                    "py-2 pr-4 text-right tabular-nums font-medium",
                    isPositive
                      ? "text-green-600 dark:text-green-400"
                      : "text-red-600 dark:text-red-400",
                  )}
                >
                  {pct.toFixed(2)}%
                </td>
                <td className="w-1/2 py-2">
                  <div className="h-4 w-full rounded-sm bg-muted">
                    <div
                      className={cn(
                        "h-full rounded-sm transition-all",
                        isPositive
                          ? "bg-green-500/70 dark:bg-green-500/50"
                          : "bg-red-500/70 dark:bg-red-500/50",
                      )}
                      style={{ width: `${(barWidth * 100).toFixed(1)}%` }}
                    />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

interface RiskTableProps {
  data: RiskDecompositionResponse;
}

function RiskTable({ data }: RiskTableProps) {
  const { t } = useTranslation("common");

  const entries = useMemo(() => {
    return Object.entries(data.contributions).sort(
      ([, a], [, b]) => Math.abs(b.riskPct) - Math.abs(a.riskPct),
    );
  }, [data.contributions]);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="pb-2 pr-4 font-medium text-muted-foreground">
              {t("qlib.symbol")}
            </th>
            <th className="pb-2 pr-4 text-right font-medium text-muted-foreground">
              {t("qlib.weight")}
            </th>
            <th className="pb-2 pr-4 text-right font-medium text-muted-foreground">
              {t("qlib.riskContribution")}
            </th>
            <th className="pb-2 text-right font-medium text-muted-foreground">
              {t("qlib.riskPct")}
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([symbol, contrib]) => (
            <tr
              key={symbol}
              className="border-b border-border/50 last:border-0"
            >
              <td className="py-2 pr-4 font-mono text-xs font-medium">
                {symbol}
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {(contrib.weight * 100).toFixed(2)}%
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {(contrib.riskContribution * 100).toFixed(4)}%
              </td>
              <td
                className={cn(
                  "py-2 text-right font-medium tabular-nums",
                  contrib.riskPct > 30
                    ? "text-red-600 dark:text-red-400"
                    : "text-muted-foreground",
                )}
              >
                {contrib.riskPct.toFixed(2)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-3 text-xs text-muted-foreground">
        {t("qlib.volatility")}: {(data.portfolioVolatility * 100).toFixed(2)}%
        {" | "}
        {t("qlib.dataDays")}: {data.dataDays}
      </div>
    </div>
  );
}

interface EfficientFrontierChartProps {
  data: EfficientFrontierResponse;
  currentPortfolio: {
    expectedReturn: number;
    volatility: number;
  };
}

function EfficientFrontierChart({
  data,
  currentPortfolio,
}: EfficientFrontierChartProps) {
  const { t } = useTranslation("common");

  const { xMin, xMax, yMin, yMax } = useMemo(() => {
    const vols = data.frontier.map((p) => p.volatility * 100);
    const rets = data.frontier.map((p) => p.expectedReturn * 100);
    const currentVol = currentPortfolio.volatility * 100;
    const currentRet = currentPortfolio.expectedReturn * 100;

    const rawXMin = Math.min(...vols, currentVol);
    const rawXMax = Math.max(...vols, currentVol);
    const rawYMin = Math.min(...rets, currentRet);
    const rawYMax = Math.max(...rets, currentRet);

    // Add 10% padding using absolute range (safe with negative values)
    const xPad = Math.max((rawXMax - rawXMin) * 0.1, 0.5);
    const yPad = Math.max((rawYMax - rawYMin) * 0.1, 0.5);

    return {
      xMin: rawXMin - xPad,
      xMax: rawXMax + xPad,
      yMin: rawYMin - yPad,
      yMax: rawYMax + yPad,
    };
  }, [data.frontier, currentPortfolio]);

  const width = 600;
  const height = 400;
  const padding = { top: 20, right: 20, bottom: 50, left: 60 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  const scaleX = (val: number) =>
    padding.left + ((val - xMin) / (xMax - xMin)) * chartWidth;
  const scaleY = (val: number) =>
    padding.top + chartHeight - ((val - yMin) / (yMax - yMin)) * chartHeight;

  const pathD = useMemo(() => {
    const sx = (val: number) =>
      padding.left + ((val - xMin) / (xMax - xMin)) * chartWidth;
    const sy = (val: number) =>
      padding.top + chartHeight - ((val - yMin) / (yMax - yMin)) * chartHeight;

    return data.frontier
      .map((point, i) => {
        const x = sx(point.volatility * 100);
        const y = sy(point.expectedReturn * 100);
        return i === 0 ? `M ${x} ${y}` : `L ${x} ${y}`;
      })
      .join(" ");
  }, [data.frontier, xMin, xMax, yMin, yMax]);

  const xTicks = useMemo(() => {
    const ticks = [];
    const step = (xMax - xMin) / 5;
    for (let i = 0; i <= 5; i++) {
      ticks.push(xMin + i * step);
    }
    return ticks;
  }, [xMin, xMax]);

  const yTicks = useMemo(() => {
    const ticks = [];
    const step = (yMax - yMin) / 5;
    for (let i = 0; i <= 5; i++) {
      ticks.push(yMin + i * step);
    }
    return ticks;
  }, [yMin, yMax]);

  return (
    <div className="flex flex-col items-center">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ aspectRatio: "3 / 2" }}
        role="img"
        aria-label={t("qlib.efficientFrontier")}
      >
        {/* Grid lines */}
        {xTicks.map((tick) => (
          <line
            key={`x-grid-${tick}`}
            x1={scaleX(tick)}
            y1={padding.top}
            x2={scaleX(tick)}
            y2={padding.top + chartHeight}
            className="stroke-muted-foreground/20"
            strokeWidth="1"
          />
        ))}
        {yTicks.map((tick) => (
          <line
            key={`y-grid-${tick}`}
            x1={padding.left}
            y1={scaleY(tick)}
            x2={padding.left + chartWidth}
            y2={scaleY(tick)}
            className="stroke-muted-foreground/20"
            strokeWidth="1"
          />
        ))}

        {/* Frontier line */}
        <path
          d={pathD}
          fill="none"
          className="stroke-blue-500 dark:stroke-blue-400"
          strokeWidth="2"
        />

        {/* Frontier points */}
        {data.frontier.map((point) => (
          <circle
            key={`fp-${point.volatility}-${point.expectedReturn}`}
            cx={scaleX(point.volatility * 100)}
            cy={scaleY(point.expectedReturn * 100)}
            r="3"
            className="fill-blue-500 dark:fill-blue-400"
          />
        ))}

        {/* Current portfolio point */}
        <circle
          cx={scaleX(currentPortfolio.volatility * 100)}
          cy={scaleY(currentPortfolio.expectedReturn * 100)}
          r="6"
          className="fill-green-500 dark:fill-green-400 stroke-background"
          strokeWidth="2"
        />

        {/* X-axis */}
        <line
          x1={padding.left}
          y1={padding.top + chartHeight}
          x2={padding.left + chartWidth}
          y2={padding.top + chartHeight}
          className="stroke-foreground"
          strokeWidth="1"
        />
        {xTicks.map((tick) => (
          <g key={`x-tick-${tick}`}>
            <line
              x1={scaleX(tick)}
              y1={padding.top + chartHeight}
              x2={scaleX(tick)}
              y2={padding.top + chartHeight + 5}
              className="stroke-foreground"
              strokeWidth="1"
            />
            <text
              x={scaleX(tick)}
              y={padding.top + chartHeight + 20}
              textAnchor="middle"
              className="fill-muted-foreground text-[10px]"
            >
              {tick.toFixed(1)}%
            </text>
          </g>
        ))}
        <text
          x={padding.left + chartWidth / 2}
          y={height - 10}
          textAnchor="middle"
          className="fill-foreground text-xs font-medium"
        >
          {t("qlib.volatility")}
        </text>

        {/* Y-axis */}
        <line
          x1={padding.left}
          y1={padding.top}
          x2={padding.left}
          y2={padding.top + chartHeight}
          className="stroke-foreground"
          strokeWidth="1"
        />
        {yTicks.map((tick) => (
          <g key={`y-tick-${tick}`}>
            <line
              x1={padding.left - 5}
              y1={scaleY(tick)}
              x2={padding.left}
              y2={scaleY(tick)}
              className="stroke-foreground"
              strokeWidth="1"
            />
            <text
              x={padding.left - 10}
              y={scaleY(tick)}
              textAnchor="end"
              dominantBaseline="middle"
              className="fill-muted-foreground text-[10px]"
            >
              {tick.toFixed(1)}%
            </text>
          </g>
        ))}
        <text
          x={15}
          y={padding.top + chartHeight / 2}
          textAnchor="middle"
          transform={`rotate(-90, 15, ${padding.top + chartHeight / 2})`}
          className="fill-foreground text-xs font-medium"
        >
          {t("qlib.expectedReturn")}
        </text>
      </svg>

      {/* Legend */}
      <div className="mt-4 flex gap-6 text-xs">
        <div className="flex items-center gap-2">
          <div className="h-2 w-6 rounded bg-blue-500 dark:bg-blue-400" />
          <span className="text-muted-foreground">
            {t("qlib.frontierPoints")}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="h-3 w-3 rounded-full bg-green-500 dark:bg-green-400" />
          <span className="text-muted-foreground">
            {t("qlib.currentPortfolio")}
          </span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function PortfolioOptimizer() {
  const { t } = useTranslation("common");

  // --- Form state ---
  const [symbolsInput, setSymbolsInput] = useState("");
  const [method, setMethod] = useState<OptimizationMethod>("max_sharpe");
  const [lookbackDays, setLookbackDays] = useState(252);
  const [targetReturn, setTargetReturn] = useState(0.1);

  // --- Result state ---
  const [optimizeResult, setOptimizeResult] =
    useState<PortfolioOptimizeResponse | null>(null);
  const [riskResult, setRiskResult] =
    useState<RiskDecompositionResponse | null>(null);
  const [frontierResult, setFrontierResult] =
    useState<EfficientFrontierResponse | null>(null);

  // --- Memoized symbol count to avoid re-parsing on every render ---
  const symbolCount = useMemo(
    () => parseSymbolsFromInput(symbolsInput).length,
    [symbolsInput],
  );

  // --- Mutations ---
  const optimizeMutation = useMutation({
    mutationFn: qlibApi.optimizePortfolio,
    onSuccess: (data) => {
      setOptimizeResult(data);
      riskMutation.mutate({
        symbols: data.symbols,
        weights: data.weights,
        lookbackDays,
      });
      frontierMutation.mutate({
        symbols: data.symbols,
        nPoints: 50,
        lookbackDays,
        constraints: {},
      });
    },
    onError: () => {
      setOptimizeResult(null);
      setRiskResult(null);
      setFrontierResult(null);
    },
  });

  const riskMutation = useMutation({
    mutationFn: qlibApi.getRiskDecomposition,
    onSuccess: (data) => {
      setRiskResult(data);
    },
  });

  const frontierMutation = useMutation({
    mutationFn: qlibApi.getEfficientFrontier,
    onSuccess: (data) => {
      setFrontierResult(data);
    },
  });

  // --- Handler (no mutation in deps — call .mutate inline) ---
  const handleOptimize = useCallback(() => {
    const symbols = parseSymbolsFromInput(symbolsInput);
    if (symbols.length < 2) return;

    setRiskResult(null);
    setFrontierResult(null);
    optimizeMutation.mutate({
      symbols,
      method,
      lookbackDays,
      constraints: method === "efficient_return" ? { targetReturn } : {},
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- optimizeMutation.mutate is stable
  }, [symbolsInput, method, lookbackDays, targetReturn]);

  const isLoading =
    optimizeMutation.isPending ||
    riskMutation.isPending ||
    frontierMutation.isPending;

  // --- Method options ---
  const methodOptions: Array<{
    value: OptimizationMethod;
    label: string;
    desc: string;
  }> = [
    {
      value: "max_sharpe",
      label: t("qlib.maxSharpe"),
      desc: t("qlib.maxSharpeDesc"),
    },
    {
      value: "min_volatility",
      label: t("qlib.minVolatility"),
      desc: t("qlib.minVolatilityDesc"),
    },
    {
      value: "risk_parity",
      label: t("qlib.riskParity"),
      desc: t("qlib.riskParityDesc"),
    },
    {
      value: "efficient_return",
      label: t("qlib.efficientReturn"),
      desc: t("qlib.efficientReturnDesc"),
    },
  ];

  return (
    <div className="space-y-6">
      {/* ----------------------------------------------------------------- */}
      {/* Input Section                                                     */}
      {/* ----------------------------------------------------------------- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <TrendingUp className="h-5 w-5" />
            {t("qlib.optimizePortfolio")}
          </CardTitle>
          <CardDescription>{t("qlib.symbolsInputHelp")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {/* Symbols input */}
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="opt-symbols">{t("qlib.enterSymbols")}</Label>
              <Input
                id="opt-symbols"
                placeholder="AAPL, MSFT, GOOGL, AMZN, NVDA"
                value={symbolsInput}
                onChange={(e) => setSymbolsInput(e.target.value)}
                disabled={isLoading}
              />
            </div>

            {/* Method selector */}
            <div className="space-y-2">
              <Label htmlFor="opt-method">{t("qlib.optimizationMethod")}</Label>
              <Select
                value={method}
                onValueChange={(val) => setMethod(val as OptimizationMethod)}
                disabled={isLoading}
              >
                <SelectTrigger id="opt-method">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {methodOptions.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      <span className="flex flex-col">
                        <span>{opt.label}</span>
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Lookback days */}
            <div className="space-y-2">
              <Label htmlFor="opt-lookback">{t("qlib.lookbackDays")}</Label>
              <Input
                id="opt-lookback"
                type="number"
                min={30}
                max={1260}
                value={lookbackDays}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  if (!isNaN(v)) setLookbackDays(v);
                }}
                disabled={isLoading}
              />
            </div>

            {/* Target return — only shown for efficient_return method */}
            {method === "efficient_return" && (
              <div className="space-y-2">
                <Label htmlFor="opt-target-return">
                  {t("qlib.targetReturn")}
                </Label>
                <Input
                  id="opt-target-return"
                  type="number"
                  step={0.01}
                  min={-1}
                  max={5}
                  value={targetReturn}
                  onChange={(e) => {
                    const v = parseFloat(e.target.value);
                    if (!isNaN(v)) setTargetReturn(v);
                  }}
                  disabled={isLoading}
                />
                <p className="text-xs text-muted-foreground">
                  {t("qlib.targetReturnDesc")}
                </p>
              </div>
            )}
          </div>

          {/* Optimize button */}
          <div className="mt-4 flex items-center gap-3">
            <Button
              onClick={handleOptimize}
              disabled={isLoading || symbolCount < 2}
            >
              {optimizeMutation.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {t("qlib.optimizing")}
                </>
              ) : (
                t("qlib.optimizePortfolio")
              )}
            </Button>
            {symbolCount > 0 && symbolCount < 2 && (
              <span className="text-sm text-muted-foreground">
                {t("qlib.minSymbolsRequired")}
              </span>
            )}
          </div>

          {/* Error display */}
          {optimizeMutation.isError && (
            <div className="mt-4 flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <p className="font-medium">{t("qlib.optimizationError")}</p>
                <p className="mt-0.5 text-destructive/80">
                  {getErrorMessage(optimizeMutation.error)}
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ----------------------------------------------------------------- */}
      {/* Results Section                                                   */}
      {/* ----------------------------------------------------------------- */}
      {optimizeResult != null && (
        <div className="space-y-4">
          {/* Metric cards row */}
          <div className="grid gap-4 sm:grid-cols-3">
            <MetricCard
              label={t("qlib.expectedReturn")}
              value={formatPercent(optimizeResult.expectedReturn * 100)}
              colorClass={
                optimizeResult.expectedReturn >= 0
                  ? "text-green-600 dark:text-green-400"
                  : "text-red-600 dark:text-red-400"
              }
            />
            <MetricCard
              label={t("qlib.volatility")}
              value={`${(optimizeResult.annualVolatility * 100).toFixed(2)}%`}
            />
            <MetricCard
              label={t("qlib.sharpeRatio")}
              value={optimizeResult.sharpeRatio.toFixed(3)}
              colorClass={
                optimizeResult.sharpeRatio >= 1
                  ? "text-green-600 dark:text-green-400"
                  : optimizeResult.sharpeRatio < 0
                    ? "text-red-600 dark:text-red-400"
                    : undefined
              }
            />
          </div>

          {/* Weights, Risk, and Frontier tabs */}
          <Card>
            <Tabs defaultValue="weights">
              <CardHeader className="pb-0">
                <div className="flex items-center justify-between">
                  <TabsList>
                    <TabsTrigger value="weights" className="gap-1.5">
                      <BarChart3 className="h-3.5 w-3.5" />
                      {t("qlib.weights")}
                    </TabsTrigger>
                    <TabsTrigger value="risk" className="gap-1.5">
                      <ShieldAlert className="h-3.5 w-3.5" />
                      {t("qlib.riskDecomposition")}
                    </TabsTrigger>
                    <TabsTrigger value="frontier" className="gap-1.5">
                      <LineChart className="h-3.5 w-3.5" />
                      {t("qlib.efficientFrontier")}
                    </TabsTrigger>
                  </TabsList>
                  <span className="text-xs text-muted-foreground">
                    {t("qlib.dataDays")}: {optimizeResult.dataDays}
                  </span>
                </div>
              </CardHeader>
              <CardContent className="pt-4">
                <TabsContent value="weights" className="mt-0">
                  <WeightsTable weights={optimizeResult.weights} />
                </TabsContent>
                <TabsContent value="risk" className="mt-0">
                  {riskMutation.isPending && (
                    <div className="flex h-[120px] items-center justify-center gap-2">
                      <Loader2 className="h-5 w-5 animate-spin" />
                      <span className="text-muted-foreground">
                        {t("status.loading")}
                      </span>
                    </div>
                  )}
                  {riskMutation.isError && (
                    <div className="flex flex-col items-center justify-center gap-3 py-8 text-center">
                      <AlertCircle className="h-8 w-8 text-destructive" />
                      <p className="text-sm text-muted-foreground">
                        {getErrorMessage(riskMutation.error)}
                      </p>
                    </div>
                  )}
                  {riskResult != null && <RiskTable data={riskResult} />}
                  {!riskMutation.isPending &&
                    !riskMutation.isError &&
                    riskResult == null && (
                      <div className="flex h-[120px] items-center justify-center text-sm text-muted-foreground">
                        {t("qlib.noResults")}
                      </div>
                    )}
                </TabsContent>
                <TabsContent value="frontier" className="mt-0">
                  {frontierMutation.isPending && (
                    <div className="flex h-[120px] items-center justify-center gap-2">
                      <Loader2 className="h-5 w-5 animate-spin" />
                      <span className="text-muted-foreground">
                        {t("status.loading")}
                      </span>
                    </div>
                  )}
                  {frontierMutation.isError && (
                    <div className="flex flex-col items-center justify-center gap-3 py-8 text-center">
                      <AlertCircle className="h-8 w-8 text-destructive" />
                      <p className="text-sm text-muted-foreground">
                        {getErrorMessage(frontierMutation.error)}
                      </p>
                    </div>
                  )}
                  {frontierResult != null && (
                    <EfficientFrontierChart
                      data={frontierResult}
                      currentPortfolio={{
                        expectedReturn: optimizeResult.expectedReturn,
                        volatility: optimizeResult.annualVolatility,
                      }}
                    />
                  )}
                  {!frontierMutation.isPending &&
                    !frontierMutation.isError &&
                    frontierResult == null && (
                      <div className="flex h-[120px] items-center justify-center text-sm text-muted-foreground">
                        {t("qlib.noResults")}
                      </div>
                    )}
                </TabsContent>
              </CardContent>
            </Tabs>
          </Card>
        </div>
      )}

      {/* Empty state when no results yet */}
      {optimizeResult == null && !optimizeMutation.isPending && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 text-center">
            <TrendingUp className="mb-3 h-10 w-10 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              {t("qlib.noResults")}
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
