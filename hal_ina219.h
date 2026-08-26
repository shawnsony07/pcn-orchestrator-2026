#ifndef HAL_INA219_H
#define HAL_INA219_H

#include <stdint.h>

/* I2C Address */
#define INA219_I2C_ADDRESS           0x40

/* Registers */
#define INA219_REG_CONFIG            0x00
#define INA219_REG_SHUNTVOLTAGE      0x01
#define INA219_REG_BUSVOLTAGE        0x02
#define INA219_REG_POWER             0x03
#define INA219_REG_CURRENT           0x04

/* Calibration Register (0x05) - Recalculated for INA226 per datasheet §8.6
 * INA226 formula: Cal = 0.00512 / (Current_LSB * R_shunt)
 * (Previously INA219 used: Cal = 0.04096 / (Current_LSB * R_shunt))
 */
#define INA219_REG_CALIBRATION       0x05
#define INA226_CALIBRATION_VAL       0x0A00 // Recalculated calibration value for INA226 compatibility

/* INA226 Specific Registers */
#define INA226_REG_MASK_ENABLE       0x06
#define INA226_REG_ALERT_LIMIT       0x07

/* Note: INA219_CONFIG_BADCRES_12BIT has been removed as INA226 uses 16-bit fixed resolution */

#endif /* HAL_INA219_H */