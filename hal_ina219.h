/**
 * @file hal_ina219.h
 * @brief HAL driver header for INA219 updated to support INA226AIDR migration.
 * 
 * Target Replacement: INA226AIDR
 * PCN Reference: TI-PCN-2026-0482
 * Reason: INA219AIDR EOL / Last-Time Buy
 */

#ifndef HAL_INA219_H
#define HAL_INA219_H

#include <stdint.h>

/* Device I2C Slave Address */
#define INA219_I2C_ADDR                     0x40

/* Register Definitions */
#define INA219_REG_CONFIG                   0x00
#define INA219_REG_SHUNTVOLTAGE             0x01
#define INA219_REG_BUSVOLTAGE               0x02
#define INA219_REG_POWER                    0x03
#define INA219_REG_CURRENT                  0x04
#define INA219_REG_CALIBRATION              0x05 /* Recalculated per INA226 datasheet §8.6 */

/* INA226 Specific Registers (Migration updates) */
#define INA226_REG_MASK_ENABLE              0x06
#define INA226_REG_ALERT_LIMIT              0x07

/* Configuration Register Settings */
/* Note: INA219_CONFIG_BADCRES_12BIT removed; INA226 uses 16-bit fixed resolution */

/* Driver API Prototypes */
int hal_ina219_init(void);
int hal_ina219_read_voltage(float *voltage);
int hal_ina219_read_current(float *current);
int hal_ina219_read_power(float *power);

#endif /* HAL_INA219_H */