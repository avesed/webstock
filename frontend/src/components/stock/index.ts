// Stock detail page components
export { StockStatsGrid } from './StockStatsGrid'
export { MetalStatsGrid } from './MetalStatsGrid'
export { CommodityInfo } from './CommodityInfo'
export { FinancialsGrid } from './FinancialsGrid'
export { CompanyInfo } from './CompanyInfo'
export { NewsList } from './NewsList'

// Context for shared chat state between Widget and AI Tab
export {
  StockChatProvider,
  useStockChatContext,
  useStockChatState,
  useStockChatActions,
  useIsInStockChatProvider,
} from './StockChatContext'
export type { StockChatState, StockChatActions } from './StockChatContext'

// Tab containers
export { TraditionalTab } from './TraditionalTab'
export { AITab } from './AITab'
export { AITabChat } from './AITabChat'
export { AITabExtension } from './AITabExtension'
