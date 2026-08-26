#ifndef HAL_INA219_H
#define HAL_INA219_H

#include <stdint.h>

/* I2C Address of INA226/INA219 (Default) */
#define INA219_I2C_ADDRESS             0x40

/* INA226 Register Map (Updated from INA219 for compatibility) */
#define INA219_REG_CONFIG              0x00
#define INA219_REG_SHUNTVOLTAGE        0x01
#define INA219_REG_BUSVOLTAGE          0x02
#define INA219_REG_POWER               0x03
#define INA219_REG_CURRENT             0x04
#define INA219_REG_CALIBRATION         0x05  /* Recalculate per INA226 datasheet §8.6 */
#define INA226_REG_MASK_ENABLE         0x06  /* Added for INA226 support */
#define INA226_REG_ALERT_LIMIT         0x07  /* Added for INA226 support */

/* Configuration Register Masks (INA226 16-bit fixed resolution) */
#define INA219_CONFIG_RESET            0x8000

/* Note: INA219_CONFIG_BADCRES_12BIT has been removed as INA226 uses 16-bit fixed resolution */

#endif /* HAL_INA219_H */