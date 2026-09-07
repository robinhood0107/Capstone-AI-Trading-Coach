/** Compare live holdings with open bot lots; a symbol alone does not prove ownership. */
export function holdingOwnership(quantity: number, botQuantity: number) {
  return {
    managed: Math.min(quantity, botQuantity),
    manual: Math.max(0, quantity - botQuantity),
    mismatch: botQuantity > quantity,
  };
}
