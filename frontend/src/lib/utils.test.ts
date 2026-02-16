import { describe, it, expect } from 'vitest'
import { isMetal, getAssetType, convertWeight, convertPricePerUnit } from './utils'

describe('isMetal', () => {
  it('returns true for valid metal symbols', () => {
    expect(isMetal('GC=F')).toBe(true)
    expect(isMetal('SI=F')).toBe(true)
    expect(isMetal('PL=F')).toBe(true)
    expect(isMetal('PA=F')).toBe(true)
  })

  it('returns true for lowercase metal symbols', () => {
    expect(isMetal('gc=f')).toBe(true)
    expect(isMetal('si=f')).toBe(true)
  })

  it('returns false for stock symbols', () => {
    expect(isMetal('AAPL')).toBe(false)
    expect(isMetal('SI')).toBe(false) // Stock, not silver
    expect(isMetal('GC')).toBe(false)
    expect(isMetal('0700.HK')).toBe(false)
  })

  it('returns false for invalid metal symbols', () => {
    expect(isMetal('XX=F')).toBe(false)
    expect(isMetal('GOLD')).toBe(false)
  })
})

describe('getAssetType', () => {
  it('returns metal for precious metal symbols', () => {
    expect(getAssetType('GC=F')).toBe('metal')
    expect(getAssetType('SI=F')).toBe('metal')
  })

  it('returns stock for non-metal symbols', () => {
    expect(getAssetType('AAPL')).toBe('stock')
    expect(getAssetType('MSFT')).toBe('stock')
  })
})

describe('convertWeight', () => {
  it('returns same value for same unit', () => {
    expect(convertWeight(10, 'troy_oz', 'troy_oz')).toBe(10)
  })

  it('converts troy oz to gram correctly', () => {
    const result = convertWeight(1, 'troy_oz', 'gram')
    expect(result).toBeCloseTo(31.1035, 2)
  })

  it('converts gram to troy oz correctly', () => {
    const result = convertWeight(31.1035, 'gram', 'troy_oz')
    expect(result).toBeCloseTo(1, 2)
  })

  it('converts between gram and kilogram', () => {
    // 1000 grams to kg
    const toKg = convertWeight(1000, 'gram', 'kilogram')
    expect(toKg).toBeCloseTo(1, 1)

    // 1 kg to grams
    const toGram = convertWeight(1, 'kilogram', 'gram')
    expect(toGram).toBeCloseTo(1000, 0)
  })
})

describe('convertPricePerUnit', () => {
  it('returns same price for troy oz', () => {
    expect(convertPricePerUnit(2000, 'troy_oz')).toBe(2000)
  })

  it('converts price to per gram correctly', () => {
    const result = convertPricePerUnit(2000, 'gram')
    // $2000/troy oz / 31.1035 = $64.30/gram
    expect(result).toBeCloseTo(64.3, 0)
  })

  it('converts price to per kilogram correctly', () => {
    const result = convertPricePerUnit(2000, 'kilogram')
    // $2000/troy oz / 0.0311035 = $64,301/kg
    expect(result).toBeGreaterThan(60000)
  })
})
