#ifndef HAL_BME280_H
#define HAL_BME280_H

// HAL header for BME280, updated to replacement part BME688
#define BME280_REPLACEMENT_PART "BME688"

void hal_bme280_init(void);
void hal_bme280_read_data(void);

#endif // HAL_BME280_H
