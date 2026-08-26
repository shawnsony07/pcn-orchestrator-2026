#ifndef HAL_BME280_H
#define HAL_BME280_H

#include <stdint.h>

/* BME280 I2C Address - Compatible with BME688 */
#define BME280_I2C_ADDR 0x76

/* Chip ID updated for BME688 replacement (0x61 instead of 0x60) */
#define BME280_CHIP_ID 0x61

/* Registers */
#define BME280_REG_CHIP_ID 0xD0
#define BME280_REG_RESET 0xE0
#define BME280_REG_CTRL_MEAS 0xF4

/**
 * @brief Initialize BME280 sensor (using BME688 under the hood)
 * @return 0 on success, non-zero on failure
 */
int8_t hal_bme280_init(void);

/**
 * @brief Read temperature
 * @param temp Pointer to store temperature value
 * @return 0 on success, non-zero on failure
 */
int8_t hal_bme280_read_temp(float *temp);

#endif // HAL_BME280_H