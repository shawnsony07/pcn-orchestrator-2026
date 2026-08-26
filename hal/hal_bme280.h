#ifndef HAL_BME280_H
#define HAL_BME280_H

#include <stdint.h>

/*
 * HAL for BME280 / BME688
 * Note: BME280 (Obsolete) is replaced by BME688 per PCN.
 * BME688 Chip ID is 0x61 (BME280 was 0x60).
 */

#define BME688_I2C_ADDR         0x76
#define BME688_CHIP_ID          0x61

#define BME688_REG_ID           0xD0
#define BME688_REG_RESET        0xE0
#define BME688_REG_CTRL_HUM     0xF2
#define BME688_REG_CTRL_MEAS    0xF4
#define BME688_REG_CONFIG       0xF5

// Legacy compatibility aliases for BME280
#define BME280_I2C_ADDR         BME688_I2C_ADDR
#define BME280_CHIP_ID          BME688_CHIP_ID
#define BME280_REG_ID           BME688_REG_ID
#define BME280_REG_RESET        BME688_REG_RESET
#define BME280_REG_CTRL_HUM     BME688_REG_CTRL_HUM
#define BME280_REG_CTRL_MEAS    BME688_REG_CTRL_MEAS
#define BME280_REG_CONFIG       BME688_REG_CONFIG

#endif // HAL_BME280_H
