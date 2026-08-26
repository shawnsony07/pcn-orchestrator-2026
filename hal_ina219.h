#ifndef HAL_INA219_H
#define HAL_INA219_H

#include <stdint.h>

/**
 * @file hal_ina219.h
 * @brief Hardware Abstraction Layer for INA219AIDR / INA226AIDR Current Monitor.
 * 
 * Note: This driver has been upgraded to support the INA226AIDR replacement part
 * as per PCN TI-PCN-2026-0482. The INA226 provides higher 16-bit resolution,
 * programmable alert levels, and improved accuracy.
 */

#define INA219_I2C_ADDRESS              0x40

/* Registers */
#define INA219_REG_CONFIG               0x00
#define INA219_REG_SHUNTVOLTAGE         0x01
#define INA219_REG_BUSVOLTAGE           0x02
#define INA219_REG_POWER                0x03
#define INA219_REG_CURRENT              0x04
#define INA219_REG_CALIBRATION          0x05 /* Recalculated per INA226 datasheet §8.6 */

/* INA226 Additional Registers */
#define INA226_REG_MASK_ENABLE          0x06 /* Added per PCN TI-PCN-2026-0482 */
#define INA226_REG_ALERT_LIMIT          0x07 /* Added per PCN TI-PCN-2026-0482 */

/* Configuration Options */
/* INA219_CONFIG_BADCRES_12BIT has been removed. The INA226 uses 16-bit fixed resolution. */

#endif /* HAL_INA219_H */
