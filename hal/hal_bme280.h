#ifndef HAL_BME280_H
#define HAL_BME280_H

/* 
 * HAL for BME280 Sensor
 * Note: BME280 is obsolete. Replaced by BME688 per PCN.
 */

#define BME688_I2C_ADDR 0x76
#define BME280_I2C_ADDR BME688_I2C_ADDR

int8_t hal_bme280_init(void);
int8_t hal_bme280_read_temp(float *temp);
int8_t hal_bme280_read_humidity(float *humidity);
int8_t hal_bme280_read_pressure(float *pressure);

#endif
