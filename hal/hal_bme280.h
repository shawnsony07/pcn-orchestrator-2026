#ifndef HAL_BME280_H
#define HAL_BME280_H

#include <stdint.h>

/*
 * PCN Change Notice: BME280 is Obsolete.
 * Replaced by BME688.
 * This HAL header has been updated to support BME688.
 */

// I2C Addresses
#define BME280_I2C_ADDR_PRIM   0x76
#define BME280_I2C_ADDR_SEC    0x77

#define BME688_I2C_ADDR_PRIM   0x76
#define BME688_I2C_ADDR_SEC    0x77

// Chip IDs
#define BME280_CHIP_ID         0x60
#define BME688_CHIP_ID         0x61

// Use BME688 as active chip configuration
#define ACTIVE_CHIP_ID         BME688_CHIP_ID
#define ACTIVE_I2C_ADDR        BME688_I2C_ADDR_PRIM

typedef struct {
    float temperature;
    float pressure;
    float humidity;
    float gas_resistance; // BME688 specific
} bme_sensor_data_t;

int8_t hal_sensor_init(void);
int8_t hal_sensor_read(bme_sensor_data_t *data);

#endif // HAL_BME280_H